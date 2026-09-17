"""Daily AMZN check: today's valuation percentile (저평가), RSI(14)
(과매도), and current kis-main-overseas holding status - one desktop
notification every weekday, unconditionally (no threshold gating).

Deliberately NOT part of daily.py: this also checks kis-main-overseas
holdings, which is out of daily.py's scope (its strategy account is
kis-isa). Experimental/research stage ("실험실") - notify-only, no trade
execution, no order logic reads this.

Ramp-up: amzn_price_daily accumulates one row/day, no backfill (see
quant/amzn/README.md), so RSI shows "데이터부족" for the first
quant.amzn.signal.MIN_RSI_SAMPLES trading days from a cold start, and the
valuation percentile for the first MIN_PERCENTILE_SAMPLES.

    python -m quant.amzn.scripts.check_amzn
"""
import logging
import sqlite3
import sys

from quant import accounts
from quant.amzn import signal, storage
from quant.amzn.config import AmznConfig
from quant.amzn.price_collector import collect_and_store, parse_holding
from quant.amzn.rate_limit import RateLimiter
from quant.kis_client import KisApiError
from quant.notify import notify

SYMBOL = "AMZN"
log = logging.getLogger("amzn.check_amzn")


def format_summary(rsi: float | None, per_pctl: float | None, pbr_pctl: float | None,
                    per_samples: int, rsi_samples: int,
                    holding: dict | None | str) -> str:
    """Pure formatter - no I/O - so ramp-up/no-holding/error branches are
    unit-testable without a live KIS call. `holding` is a parse_holding()
    dict, None (confirmed no position), or the string "error" (holdings
    query itself failed - reported distinctly from "no position")."""
    if per_pctl is not None or pbr_pctl is not None:
        if per_pctl is not None and pbr_pctl is not None:
            pctl = (per_pctl + pbr_pctl) / 2
        else:
            pctl = per_pctl if per_pctl is not None else pbr_pctl
        label = signal.classify_valuation_percentile(pctl)
        parts = []
        if per_pctl is not None:
            parts.append(f"PER{per_pctl:.0f}")
        if pbr_pctl is not None:
            parts.append(f"PBR{pbr_pctl:.0f}")
        val_part = f"밸류 {pctl:.0f}%ile({label}, {'·'.join(parts)})"
    else:
        val_part = f"밸류 데이터부족({per_samples}/{signal.MIN_PERCENTILE_SAMPLES}일)"

    if rsi is not None:
        rsi_part = f"RSI {rsi:.0f}({signal.classify_rsi(rsi)})"
    else:
        rsi_part = f"RSI 데이터부족({rsi_samples}/{signal.MIN_RSI_SAMPLES}일)"

    if holding == "error":
        hold_part = "보유조회 실패"
    elif holding is None:
        hold_part = "보유 없음"
    else:
        pnl = holding.get("unrealized_pnl_pct")
        pnl_part = f" 평가손익 {pnl:+.1f}%" if pnl is not None else " 손익정보없음"
        hold_part = f"보유 {holding['qty']}주{pnl_part}"

    return f"AMZN: {val_part} · {rsi_part} · {hold_part}"


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        # kis-main and kis-main-overseas share one KisClient("main") - one
        # auth covers both the valuation fetch and the holdings query below.
        client = accounts.get_client("kis-main")
        config = AmznConfig()
        rate_limiter = RateLimiter()

        row = collect_and_store(client, SYMBOL, config, rate_limiter)
        with sqlite3.connect(storage.DB_PATH) as conn:
            if row is None:  # already collected today (e.g. manual re-run)
                row = storage.latest_price_row(conn, SYMBOL)
            history = storage.price_history(conn, SYMBOL)

        closes = [h["last_price"] for h in history if h["last_price"] is not None]
        rsi = (signal.compute_rsi(closes)
               if len(closes) >= signal.MIN_RSI_SAMPLES else None)

        per_hist = [h["per"] for h in history if h["per"] is not None]
        pbr_hist = [h["pbr"] for h in history if h["pbr"] is not None]
        per_pctl = (signal.percentile_rank(per_hist, row["per"])
                    if row and row.get("per") is not None
                    and len(per_hist) >= signal.MIN_PERCENTILE_SAMPLES else None)
        pbr_pctl = (signal.percentile_rank(pbr_hist, row["pbr"])
                    if row and row.get("pbr") is not None
                    and len(pbr_hist) >= signal.MIN_PERCENTILE_SAMPLES else None)

        try:
            resp = client.holdings_overseas()
            holding = parse_holding(resp.get("output1", []), SYMBOL)
        except KisApiError as e:
            log.warning("holdings_overseas failed: %s", e)
            holding = "error"

        message = format_summary(rsi, per_pctl, pbr_pctl,
                                  len(per_hist), len(closes), holding)
        log.info(message)
        notify("quant-lab", message)

    except KisApiError as e:
        log.error("api error: %s", e)
        notify("quant-lab", f"AMZN 체크 실패 (api error): {e}")
        return 1
    except Exception as e:
        log.exception("check failed")
        notify("quant-lab", f"AMZN 체크 실패: {e}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
