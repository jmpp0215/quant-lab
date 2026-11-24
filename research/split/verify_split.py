"""Verify research/ split against the pre-split RESEARCH_LOG.md.

Usage: python research/split/verify_split.py <repo root> 50543d4

Checks the state right after the split. Sections appended to the track files later make the
count/index checks fail; to re-check the split itself, run it on a checkout of d052341
(e.g. git worktree add /tmp/split-check d052341).
Section text is compared byte for byte, except trailing newlines at a section's end
(a file's last section has one fewer blank line than a middle one).
"""
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(sys.argv[1])
PRE = sys.argv[2]
R = ROOT / "research"
sys.path.insert(0, str(Path(__file__).parent))
from split_log import NUM_RE, split  # noqa: E402

fails = []


def check(ok, msg):
    print(("OK   " if ok else "FAIL ") + msg)
    if not ok:
        fails.append(msg)


def norm(s):
    return s.rstrip("\n") + "\n"


orig = subprocess.run(["git", "-C", str(ROOT), "show", f"{PRE}:quant/pead/RESEARCH_LOG.md"],
                      capture_output=True, text=True, check=True).stdout
opre, osecs = split(orig)
tracks = sorted(p for p in R.glob("*.md") if p.name != "INDEX.md")  # top level only, not split/
new = {}  # file -> (pre, secs)
for p in tracks:
    new[p.name] = split(p.read_text())
nsecs = [(f, h, b) for f, (_, ss) in new.items() for h, b in ss]

# 1. section count: original minus §0 == sum over track files
o_non0 = [s for s in osecs if not s[0].startswith("## 0. ")]
check(len(osecs) - 1 == len(nsecs) and len(o_non0) == len(nsecs),
      f"절 개수: 원본 {len(osecs)} (§0 제외 {len(o_non0)}) = 트랙 합 {len(nsecs)}")

# 2. byte-equal sections, each exactly once
pool = Counter(norm(b) for _, _, b in nsecs)
seen73 = 0
for h, b in o_non0:
    want = norm(b)
    if h.startswith("## 73. "):
        seen73 += 1
        if seen73 == 2:  # the one allowed change: heading line only
            want = want.replace("## 73. ", "## 73b. ", 1)
            check(want.split("\n", 1)[1] == norm(b).split("\n", 1)[1], "§73b: 제목 줄 외 본문 동일")
    if pool[want] != 1:
        check(False, f"절 불일치 또는 중복: {h[:60]}")
    pool[want] -= 1
check(all(v == 0 for v in pool.values()), "모든 원본 절이 트랙 파일에 정확히 한 번씩 (바이트 비교)")
check(new["pead_early.md"][0] == opre,
      "머리말(# PEAD 연구 로그 + 상태)이 pead_early.md 맨 위에 그대로")

# 3. §0 verbatim in INDEX
s0 = norm([b for h, b in osecs if h.startswith("## 0. ")][0])
idx = (R / "INDEX.md").read_text()
check(s0 in idx, "§0 원문이 INDEX.md에 그대로 포함")

# 4. no duplicate headings / numbers across tracks
hc = Counter(h for _, h, _ in nsecs)
nums = Counter(NUM_RE.match(h).group(1) for _, h, _ in nsecs if NUM_RE.match(h))
check(not [h for h, c in hc.items() if c > 1], "중복 절 제목 0개")
check(not [n for n, c in nums.items() if c > 1], f"중복 절 번호 0개 (번호 있는 절 {len(nums)}개)")

# 5. index table rows match actual locations
rows = re.findall(r"^\| (§[\w.]+|—) \| \[([\w.]+)\]\([\w.]+\) \| (.*) \|$", idx, re.M)
actual = Counter()
for f, h, _ in nsecs:
    m = NUM_RE.match(h)
    actual[("§" + m.group(1) if m else "—", f, (h[m.end():] if m else h[3:]).replace("|", "\\|"))] += 1
check(Counter(rows) == actual and len(rows) == len(nsecs), f"색인 표 {len(rows)}행 = 실제 절 위치 {len(nsecs)}개")

# 6. old path gone; no stale references in tracked files (scratch/_final excluded by being untracked)
check(not (ROOT / "quant/pead/RESEARCH_LOG.md").exists(), "옛 경로 파일 없음")
g = subprocess.run(["git", "-C", str(ROOT), "grep", "-n", "-I", "-E", "RESEARCH_LOG", "--", ".",
                    ":!research/INDEX.md", ":!research/split/"], capture_output=True, text=True).stdout
check(g == "", "추적 파일에 RESEARCH_LOG 참조 없음 (INDEX의 옛 경로 안내 제외)" + ("" if not g else "\n" + g))
g2 = subprocess.run(["git", "-C", str(ROOT), "grep", "-n", "RESEARCH_LOG"], capture_output=True, text=True).stdout
print("     INDEX.md의 옛 경로 언급:", len([ln for ln in g2.splitlines() if ln.startswith("research/INDEX.md")]), "줄")

print("\n결과:", "통과" if not fails else f"실패 {len(fails)}건")
sys.exit(1 if fails else 0)
