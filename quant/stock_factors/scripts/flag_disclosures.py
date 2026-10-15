"""Record DART bad-news flags for the latest PBR rebalance's targets.

Record-only (AI study doc, candidate A): never reads or places orders.
Run by launchd after pbrcheck; a no-op when the latest full rebalance is
already recorded. Review-class filings are judged by an LLM through the
Claude Code CLI (`claude -p`, the user's subscription - no API key), one
call per symbol to amortise the CLI's ~1.6k-token fixed overhead.

    python quant/stock_factors/scripts/flag_disclosures.py              latest full rebalance
    python quant/stock_factors/scripts/flag_disclosures.py --dry-run    print only, no DB writes
    python quant/stock_factors/scripts/flag_disclosures.py --date 2026-09-14 [--force]
"""

import argparse
import io
import json
import logging
import shutil
import subprocess
import sys
import tempfile
import zipfile
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.append(str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import requests  # noqa: E402

from quant import logging_config  # noqa: E402
from quant.pead import dart  # noqa: E402
from quant.stock_factors import disclosures  # noqa: E402
from quant.stock_factors.scripts import pbr_storage  # noqa: E402

log = logging.getLogger("flag_disclosures")

# Pinned for the whole experiment - changing it changes what "severe" means
# (see the study doc's model-change rule).
LLM_MODEL = "claude-haiku-5-5"
# launchd's PATH has no /opt/homebrew/bin.
CLAUDE_BIN = shutil.which("claude") or "/opt/homebrew/bin/claude"
LLM_TIMEOUT_SECS = 300
# First rebalance of the pre-registered forward record. The scheduled run
# never records an earlier one (an explicit --date still can).
START_DATE = "2026-10-16"


def list_filings(corp_code: str, bgn_de: str, end_de: str) -> list[dict]:
    out, page = [], 1
    while True:
        r = dart._rate_limit_and_retry(f"{dart.OPENDART_BASE_URL}/list.json", {
            "crtfc_key": dart._get_api_key(), "corp_code": corp_code,
            "bgn_de": bgn_de, "end_de": end_de, "page_no": page, "page_count": 100})
        if r.get("status") == "013":   # no filings in range
            return out
        if r.get("status") != "000":
            raise RuntimeError(f"DART list.json status {r.get('status')}: {r.get('message')}")
        out += r.get("list", [])
        if page >= int(r.get("total_page", 1)):
            return out
        page += 1


def filing_body(rcept_no: str) -> str:
    """Body text, or a marker when DART has no document for the filing
    (some attachment-only corrections and subsidiary reports return an
    error page instead of a zip) - the LLM then judges from the title."""
    res = requests.get(f"{dart.OPENDART_BASE_URL}/document.xml", params={
        "crtfc_key": dart._get_api_key(), "rcept_no": rcept_no}, timeout=60)
    res.raise_for_status()
    try:
        with zipfile.ZipFile(io.BytesIO(res.content)) as z:
            raw = z.read(z.namelist()[0]).decode("utf-8", "ignore")
    except zipfile.BadZipFile:
        log.warning("%s: no DART document, judging from the title", rcept_no)
        return disclosures.NO_BODY
    return disclosures.body_text(raw)


def judge(symbol: str, name: str, review: list[dict], stats: dict) -> dict[str, dict]:
    """LLM verdicts by rcept_no; filings the reply skipped are asked once more."""
    verdicts: dict[str, dict] = {}
    pending = review
    for _ in range(2):
        text, usage = ask_llm(disclosures.build_prompt(symbol, name, pending))
        stats["n_llm_calls"] += 1
        for k in ("input_tokens", "output_tokens", "cost_usd"):
            stats[k] += usage[k]
        verdicts |= disclosures.parse_reply(text, [r["rcept_no"] for r in pending])
        pending = [r for r in pending if r["rcept_no"] not in verdicts]
        if not pending:
            break
    return verdicts


def ask_llm(prompt: str) -> tuple[str, dict]:
    """Reply text and {input_tokens, output_tokens, cost_usd}. Runs in an
    empty temp dir so no project CLAUDE.md rides along."""
    with tempfile.TemporaryDirectory() as cwd:
        proc = subprocess.run(
            [CLAUDE_BIN, "-p", "--model", LLM_MODEL, "--output-format", "json",
             "--tools", "", "--system-prompt", disclosures.SYSTEM_PROMPT,
             "--strict-mcp-config", "--no-session-persistence"],
            input=prompt, capture_output=True, text=True, cwd=cwd,
            timeout=LLM_TIMEOUT_SECS)
    if proc.returncode != 0:
        raise RuntimeError(f"claude -p exit {proc.returncode}: {proc.stderr[:300] or proc.stdout[:300]}")
    out = json.loads(proc.stdout)
    if out.get("is_error"):
        raise RuntimeError(f"claude -p error: {str(out.get('result'))[:300]}")
    u = out.get("usage") or {}
    usage = {
        "input_tokens": (u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
                         + u.get("cache_read_input_tokens", 0)),
        "output_tokens": u.get("output_tokens", 0),
        "cost_usd": Decimal(str(out.get("total_cost_usd") or 0)),
    }
    return out.get("result") or "", usage


def run(trade_date: str, symbols: list[str]) -> tuple[list[dict], dict]:
    bgn, end = disclosures.window(trade_date)
    rows = []
    stats = {"n_symbols": len(symbols), "n_llm_calls": 0, "input_tokens": 0,
             "output_tokens": 0, "cost_usd": Decimal("0"), "errors": 0}
    for symbol in symbols:
        corp_code = dart._get_corp_code(symbol)
        if not corp_code:
            log.error("%s: no DART corp_code", symbol)
            stats["errors"] += 1
            continue
        filings = list_filings(corp_code, bgn, end)
        mine = [{"symbol": symbol, "rcept_no": f["rcept_no"], "rcept_dt": f["rcept_dt"],
                 "report_nm": f["report_nm"],
                 "title_class": disclosures.classify_title(f["report_nm"]),
                 "llm_severe": None, "llm_category": None, "llm_reason": None,
                 "llm_model": None} for f in filings]
        review = [r for r in mine if r["title_class"] == "review"]
        if review:
            name = filings[0]["corp_name"]
            try:
                for r in review:
                    r["body"] = filing_body(r["rcept_no"])
                judged = judge(symbol, name, review, stats)
                for r in review:
                    j = judged.get(r["rcept_no"])
                    if j is None:
                        log.error("%s %s: not judged by the LLM", symbol, r["rcept_no"])
                        stats["errors"] += 1
                        continue
                    r.update(llm_severe=j["severe"], llm_category=j["category"],
                             llm_reason=j["reason"], llm_model=LLM_MODEL)
            except Exception as e:
                log.error("%s: LLM review failed: %s", symbol, e)
                stats["errors"] += 1
            for r in review:
                r.pop("body", None)
        rows += mine
    return rows, stats


def report(trade_date: str, rows: list[dict], stats: dict) -> None:
    by_symbol: dict[str, list[dict]] = {}
    for r in rows:
        by_symbol.setdefault(r["symbol"], []).append(r)
    print(f"\n=== disclosure flags for {trade_date} "
          f"(filings {disclosures.window(trade_date)[0]}~{disclosures.window(trade_date)[1]}) ===")
    counts = {"R1": 0, "R2": 0, "L": 0}
    for symbol, rs in sorted(by_symbol.items()):
        flags = disclosures.symbol_flags(rs)
        for k in counts:
            counts[k] += bool(flags[k])
        if not flags["R2"]:
            continue
        print(f"{symbol}  R1={flags['R1']!s:5} R2={flags['R2']!s:5} L={flags['L']}")
        for r in rs:
            if r["title_class"] == "neutral":
                continue
            verdict = ("" if r["title_class"] == "severe" else
                       f" -> LLM {'SEVERE' if r['llm_severe'] else 'ok' if r['llm_severe'] is False else 'n/a'}"
                       f" [{r['llm_category']}] {r['llm_reason'] or ''}")
            print(f"    {r['rcept_dt']} {r['title_class']:6} {r['report_nm']}{verdict}")
    print(f"\nsymbols {stats['n_symbols']}, filings {len(rows)}, flagged R1={counts['R1']} "
          f"R2={counts['R2']} L={counts['L']}")
    print(f"LLM calls {stats['n_llm_calls']}, tokens in {stats['input_tokens']:,} "
          f"out {stats['output_tokens']:,}, est. cost ${stats['cost_usd']:.4f}, errors {stats['errors']}")


def main() -> int:
    logging_config.setup()
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--date")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    pbr_storage.init()
    with pbr_storage.connect() as conn:
        trade_date = args.date or pbr_storage.get_latest_full_rebalance_date(conn)
        if trade_date is None:
            log.info("no PBR rebalance recorded yet")
            return 0
        if not args.date and trade_date < START_DATE:
            log.info("latest full rebalance %s predates the record's start %s",
                     trade_date, START_DATE)
            return 0
        if not args.dry_run and not args.force and pbr_storage.disclosure_run_exists(conn, trade_date):
            log.info("disclosure flags for %s already recorded", trade_date)
            return 0
        target = pbr_storage.get_rebalance_target(conn, trade_date)
    if not target:
        log.error("no target portfolio for %s", trade_date)
        return 1

    rows, stats = run(trade_date, sorted(target))
    report(trade_date, rows, stats)
    if args.dry_run:
        print("(dry run: nothing saved)")
    else:
        with pbr_storage.connect() as conn:
            pbr_storage.save_disclosure_run(conn, trade_date, rows, stats)
        log.info("saved %d filing rows for %s", len(rows), trade_date)
    return 1 if stats["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
