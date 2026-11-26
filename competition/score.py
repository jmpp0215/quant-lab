"""Scoring CLI (RULES.md §7).

  python competition/score.py train   participants/<id>/<file>.py   # anyone, unlimited
  python competition/score.py cv      participants/<id>/<file>.py   # anyone, unlimited
  python competition/score.py public  participants/<id>/<file>.py   # own id, 3 per KST day
  python competition/score.py private --final                       # host, after DEADLINE
"""
import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import harness as h

KST = ZoneInfo("Asia/Seoul")
LOG = h.PRIVATE / "submissions.jsonl"
BOARD = h.ROOT / "LEADERBOARD.md"
PUBLIC_PER_DAY = 3
# One-off extra public submissions for every id on a KST day (RULES.md change log).
EXTRA_PUBLIC = {"2026-11-26": 1}
CV_BLOCKS = [(2006, 2007), (2008, 2009), (2010, 2011), (2012, 2013)]
MAX_FINAL = 2


def _participant(path: Path) -> str:
    parts = path.resolve().relative_to(h.ROOT).parts
    if len(parts) != 3 or parts[0] != "participants" or parts[1].startswith("_"):
        raise SystemExit("제출 파일은 competition/participants/<id>/ 바로 아래에 있어야 합니다.")
    return parts[1]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:10]


def _fmt(m: dict) -> str:
    return (f"dates {m['n_dates']}  IC {m['ic']:+.4f} (t {m['ic_t']:+.2f}, "
            f"양수 비율 {m['hit']:.0%})  증분 IC {m['inc_ic']:+.4f} "
            f"(t {m['inc_ic_t']:+.2f})  상위 절반-바구니 {m['spread_ann_pp']:+.2f}%p/년")


def _replace(text: str, tag: str, body: str) -> str:
    pat = re.compile(rf"(<!-- {tag}:START -->\n).*?(<!-- {tag}:END -->)", re.S)
    return pat.sub(lambda m: m.group(1) + body + m.group(2), text)


def _append_row(tag: str, row: str) -> None:
    text = BOARD.read_text()
    BOARD.write_text(text.replace(f"<!-- {tag}:END -->", f"{row}\n<!-- {tag}:END -->"))


def train(path: Path) -> None:
    per = h.run(path, "train")
    print(_fmt(h.summary(per)))
    per["year"] = [d.year for d in per["date"]]
    print(per.groupby("year")["ic"].agg(["count", "mean"]).round(4).to_string())


def cv(path: Path) -> None:
    """Train-only cross-check: two-year blocks, plus the one-snapshot delay check."""
    data = h.load("train")
    per = h.run(path, "train", data=data)
    per["year"] = [d.year for d in per["date"]]
    print(f"{'블록':<11}{'dates':>6}{'IC':>9}{'t':>7}{'증분 IC':>9}{'t':>7}{'%p/년':>8}")
    ics = []
    for a, b in CV_BLOCKS:
        m = h.summary(per[per["year"].between(a, b)].reset_index(drop=True))
        ics.append(m["ic"])
        print(f"{a}-{b:<6}{m['n_dates']:>6}{m['ic']:>+9.4f}{m['ic_t']:>+7.2f}"
              f"{m['inc_ic']:>+9.4f}{m['inc_ic_t']:>+7.2f}{m['spread_ann_pp']:>+8.2f}")
    print(f"전체: {_fmt(h.summary(per))}")
    print(f"양수 블록 {sum(x > 0 for x in ics)}/{len(ics)}, 가장 나쁜 블록 IC {min(ics):+.4f}")
    lag = h.summary(h.run(path, "train", lag=1, data=data))
    print(f"지연 점검(lag 1, train): IC {lag['ic']:+.4f} (t {lag['ic_t']:+.2f})")


def public(path: Path) -> None:
    pid = _participant(path)
    now = datetime.now(KST)
    today = now.date().isoformat()
    past = [json.loads(x) for x in LOG.read_text().splitlines()] if LOG.exists() else []
    used = sum(1 for r in past if r["id"] == pid and r["split"] == "public"
               and r["time"][:10] == today)
    limit = PUBLIC_PER_DAY + EXTRA_PUBLIC.get(today, 0)
    if used >= limit:
        raise SystemExit(f"{pid}: 오늘 public 제출 {limit}회를 다 썼습니다.")
    m = h.summary(h.run(path, "public"))
    rec = {"id": pid, "split": "public", "time": now.isoformat(timespec="seconds"),
           "file": path.name, "sha": _sha(path), "ic": round(m["ic"], 4),
           "t": round(m["ic_t"], 2)}
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    _append_row("PUBLIC", f"| {pid} | {rec['time'][:16]} | {path.name} | "
                          f"`{rec['sha']}` | {m['ic']:+.4f} | {m['ic_t']:+.2f} |")
    print(f"public IC {m['ic']:+.4f} (t {m['ic_t']:+.2f}, 결정일 {m['n_dates']}개)  "
          f"(오늘 {used + 1}/{limit}회)")


def private_final() -> None:
    if datetime.now(KST).date().isoformat() <= h.DEADLINE:
        raise SystemExit(f"비공개 채점은 마감({h.DEADLINE}) 다음 날부터입니다.")
    data = {s: h.load(s) for s in ("train", "private")}
    data["public"] = data["private"]
    rows = []
    for folder in sorted((h.ROOT / "participants").iterdir()):
        final = folder / "FINAL.txt"
        if folder.name.startswith("_") or not final.exists():
            continue
        names = [x.strip() for x in final.read_text().splitlines() if x.strip()]
        for name in names[:MAX_FINAL]:
            path = folder / name
            res = {s: h.summary(h.run(path, s, data=data[s]))
                   for s in ("train", "public", "private")}
            lag = h.summary(h.run(path, "private", lag=1, data=data["private"]))
            p = res["private"]
            ok = p["ic"] > 0 and p["ic_t"] >= 2 and lag["ic"] > 0
            new = ok and p["inc_ic"] > 0 and p["inc_ic_t"] >= 2
            verdict = "새 정보" if new else ("통과" if ok else "미통과")
            rows.append((p["ic"], f"| {folder.name} | {name} | `{_sha(path)}` | "
                         f"{res['train']['ic']:+.4f} | {res['public']['ic']:+.4f} | "
                         f"**{p['ic']:+.4f}** (t {p['ic_t']:+.2f}) | {lag['ic']:+.4f} | "
                         f"{p['inc_ic']:+.4f} (t {p['inc_ic_t']:+.2f}) | "
                         f"{p['spread_ann_pp']:+.2f} | {verdict} |"))
            with (folder / "PRIVATE_RESULT.md").open("a") as f:
                f.write(f"\n## {name} (`{_sha(path)}`, {datetime.now(KST):%Y-%m-%d})\n")
                for s, m in res.items():
                    f.write(f"- {s}: {_fmt(m)}\n")
                f.write(f"- private 지연 점검(lag 1): {_fmt(lag)}\n- 판정: {verdict}\n")
    rows.sort(key=lambda r: -r[0])
    body = "".join(f"| {i} |{r[1][1:]}\n" for i, r in enumerate(rows, 1))
    BOARD.write_text(_replace(BOARD.read_text(), "PRIVATE", body))
    print(body)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("split", choices=["train", "cv", "public", "private"])
    ap.add_argument("path", nargs="?", type=Path)
    ap.add_argument("--final", action="store_true")
    a = ap.parse_args()
    if a.split == "private":
        if not a.final:
            raise SystemExit("비공개는 마감 후 --final로 한 번에만 채점합니다.")
        private_final()
    elif a.path is None:
        raise SystemExit("제출 파일 경로를 주세요.")
    elif a.split == "train":
        train(a.path)
    elif a.split == "cv":
        cv(a.path)
    else:
        public(a.path)


if __name__ == "__main__":
    main()
