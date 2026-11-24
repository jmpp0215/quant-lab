"""Split quant/pead/RESEARCH_LOG.md into research/<track>.md + research/INDEX.md.

Usage: python split_log.py <repo root> <pre-split commit>
Moves text only. The one textual change is the second "## 73." heading -> "## 73b.".

Ran once on 2026-11-25 (split commit d052341, pre-split 50543d4). Kept as the record of how
the split was made; it refuses to run while research/ has track files, since rerunning would
overwrite sections written after the split. verify_split.py imports split() from here.
"""
import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
PRE = sys.argv[2]
SRC = ROOT / "quant/pead/RESEARCH_LOG.md"
OUT = ROOT / "research"

TRACKS = {  # file -> (header title, description)
    "pead_early.md": None,  # keeps the original preamble as its header
    "pbr.md": ("저PBR 실전 전략 (토스 PBR Top 50)", "PBR 팩터 검증, 실전 PBR 규칙의 근거와 변경 시도"),
    "sector.md": ("업종 밸류·로테이션", "업종 PBR 보정, 업종 로테이션, 업종 간 PBR 격차"),
    "momentum_etf.md": ("ISA 듀얼 모멘텀 (ETF 유니버스)", "모멘텀 유니버스, 선택 시점, 무거래 구간"),
    "fear_losers.md": ("공포 때 급락주", "극단적 공포 때 급락주 반등, 전향 기록"),
    "market_regime.md": ("시장 국면·타이밍", "시장 폭, 밸류에이션, 정치 일정과 이후 코스피"),
    "value_spread.md": ("가치 스프레드", "가치 스프레드와 이후 PBR 성과, 입금 배분"),
    "cross.md": ("여러 트랙에 걸친 기록", "전략 조합, 실거래 대 백테스트, 일괄 재실행, 세션 요약"),
}

NUM_TRACK = {}
for f, nums in {
    "pead_early.md": [*range(1, 10)],
    "pbr.md": [*range(10, 30), 36, 52, 53, 54, 55, 56, 60, 63, *range(65, 73)],
    "sector.md": [30, 31, 32, 33, 34, 62],
    "momentum_etf.md": [37, 64, 73, 74, 75],
    "fear_losers.md": [38, 39, 40, 41, 42, 49, 50, 76],
    "market_regime.md": [43, 44, 45, 47, 51],
    "value_spread.md": [46, 48, 57, 58, 59, 61],
    "cross.md": [35, 68],
}.items():
    for n in nums:
        NUM_TRACK[str(n)] = f
NUM_TRACK["12.1"] = "pbr.md"
NUM_TRACK["73b"] = "pbr.md"
NAMED_TRACK = {  # unnumbered headings, by prefix
    "결론": "pead_early.md",
    "[TODO] PEAD": "pead_early.md",
    "57~60 종합": "value_spread.md",
    "신주 발행 정정 일괄 재실행": "cross.md",
    "§37~§48 한눈에": "cross.md",
}
NUM_RE = re.compile(r"^## (\d+(?:\.\d+)?b?)\.? ")


def split(text):
    """(preamble, [(heading, body_text)]) — body_text includes the heading line."""
    lines = text.splitlines(keepends=True)
    starts = [i for i, ln in enumerate(lines) if ln.startswith("## ")]
    pre = "".join(lines[: starts[0]])
    secs = []
    for a, b in zip(starts, starts[1:] + [len(lines)], strict=True):
        secs.append((lines[a].rstrip("\n"), "".join(lines[a:b])))
    return pre, secs


def key_of(heading):
    m = NUM_RE.match(heading)
    if m:
        return m.group(1)
    rest = heading[3:]
    for p in NAMED_TRACK:
        if rest.startswith(p):
            return p
    raise ValueError(heading)


def title_of(heading):
    m = NUM_RE.match(heading)
    return heading[m.end():] if m else heading[3:]


def norm(s):
    return s.rstrip("\n") + "\n"


def main():
    if any(OUT.glob("*.md")):
        sys.exit(f"{OUT} already has track files; the split ran once (d052341). Not overwriting.")
    text = SRC.read_text()
    pre, secs = split(text)
    # rename the later "## 73." (C5 draft) to 73b
    idx73 = [i for i, (h, _) in enumerate(secs) if h.startswith("## 73. ")]
    assert len(idx73) == 2 and "C5" in secs[idx73[1]][0], idx73
    i = idx73[1]
    h, body = secs[i]
    secs[i] = (h.replace("## 73. ", "## 73b. ", 1), body.replace("## 73. ", "## 73b. ", 1))

    sec0 = [s for s in secs if s[0].startswith("## 0. ")]
    assert len(sec0) == 1
    rest = [s for s in secs if not s[0].startswith("## 0. ")]

    files = {f: [] for f in TRACKS}
    rows = []
    for h, body in rest:
        k = key_of(h)
        f = NUM_TRACK.get(k) or NAMED_TRACK[k]
        files[f].append(body)
        rows.append((k if NUM_RE.match(h) else "—", f, title_of(h)))

    OUT.mkdir(exist_ok=True)
    for f, bodies in files.items():
        if TRACKS[f] is None:
            head = pre
        else:
            t, d = TRACKS[f]
            head = (f"# {t} — 연구 기록\n\n{d}. 절 번호는 모든 트랙 파일이 함께 쓰는 하나의 순번이다. "
                    f"색인과 쓰는 법은 [INDEX.md](INDEX.md).\n\n")
        out = head + "".join(norm(b) + "\n" for b in bodies[:-1]) + norm(bodies[-1])
        (OUT / f).write_text(out)

    # index
    def sortkey(r):
        k = r[0]
        if k == "—":
            return (999, 0, "")
        m = re.match(r"(\d+)(?:\.(\d+))?(b?)", k)
        return (int(m.group(1)), int(m.group(2) or 0), m.group(3))

    table = ["| § | 파일 | 제목 |", "|---|---|---|"]
    for k, f, t in sorted(rows, key=sortkey):
        table.append(f"| {('§' + k) if k != '—' else '—'} | [{f}]({f}) | {t.replace('|', '\\|')} |")
    track_list = "\n".join(
        f"- [{f}]({f}) — " + ("PEAD(§1~§5)와 초기 개별주 팩터(§6~§9). 원래 로그의 머리말이 이 파일 맨 위에 있다"
                              if TRACKS[f] is None else f"{TRACKS[f][0]}: {TRACKS[f][1]}")
        for f in TRACKS)
    index = f"""# 연구 기록 색인

**분할**: 이 폴더의 파일들은 2026-11-25에 `quant/pead/RESEARCH_LOG.md` 한 파일을 연구 트랙별로 나눈 것이다.
분할 커밋 `SPLIT_COMMIT`, 분할 직전 커밋 `{PRE}`. 내용은 옮기기만 했고, 바뀐 글자는 두 번째 `## 73.`
(C5 기여 분해 초안)의 번호를 `## 73b.`로 바꾼 한 줄뿐이다.

**분할 이전의 사전 등록 시각은 옛 경로 `quant/pead/RESEARCH_LOG.md`의 git 이력으로 확인한다.**

```bash
git log -- quant/pead/RESEARCH_LOG.md                 # 사전 등록·결과 커밋의 시각
git show {PRE}:quant/pead/RESEARCH_LOG.md        # 분할 직전의 원본 전체
```

## 트랙 파일

{track_list}

## 분할 후 쓰는 법

아래 "0. 이 로그를 쓰는 법"은 분할 전 원문 그대로다. 그 규칙은 모두 유지하고, 파일에 관한 부분만 이렇게 읽는다.

- 새 절은 주제가 맞는 트랙 파일의 **끝에** 쓴다. 번호는 모든 트랙 파일을 통틀어 마지막 번호에 이어 붙인다(이 색인 표의 마지막 번호 + 1).
  맞는 트랙이 없으면 새 트랙 파일을 만들고 위 목록에 추가한다.
- 새 절을 쓰면 아래 "절 색인" 표에 한 줄을, 스크립트가 있으면 "섹션별 스크립트 색인"(원문 표 또는 그 아래 추가 표)에 한 줄을 넣는다.
- 원문의 "이 파일", "맨 위의 '연구 종료'"는 분할 전 한 파일을 가리킨다. "연구 종료"는 지금 [pead_early.md](pead_early.md) 맨 위에 있다.
- 사전 등록 커밋 규칙(먼저 커밋, 결과는 같은 절 아래)은 그대로다. 분할 이후 절의 등록 시각은 해당 트랙 파일의 git 이력으로 확인한다.

## 절 색인

{chr(10).join(table)}

## 섹션별 스크립트 색인 — 분할 때 추가한 줄

원문 표(아래 §0) 이후에 생긴 절이다. 본문이 인용한 `scratch/` 경로다.

| 섹션 | 스크립트·데이터 |
|---|---|
| §75 | `momproxy/s75.py`, `momproxy/out/s75_*.txt` |
| §76 | `us_fear_losers/ref.py` (참고 결과만, 전방 기록 없음) |

{norm(sec0[0][1])}"""
    (OUT / "INDEX.md").write_text(index)


if __name__ == "__main__":
    main()
