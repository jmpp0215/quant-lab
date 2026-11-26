"""Register a participant: python competition/register.py <id>

Copies participants/_template to participants/<id>/ and adds a row to LEADERBOARD.md.
<id>: lowercase letters, digits, '-', '_' (2-20 chars), used as the folder name.
"""
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent


def main() -> None:
    if len(sys.argv) != 2 or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,19}", sys.argv[1]):
        raise SystemExit("사용법: python competition/register.py <id>  "
                         "(영문 소문자·숫자·-·_, 2~20자)")
    pid = sys.argv[1]
    dest = ROOT / "participants" / pid
    if dest.exists():
        raise SystemExit(f"{pid}: 이미 등록된 id입니다.")
    today = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    shutil.copytree(ROOT / "participants/_template", dest)
    (dest / "external").mkdir()
    for f in ("submission.py", "REPORT.md"):
        p = dest / f
        p.write_text(p.read_text().replace("{{ID}}", pid).replace("{{DATE}}", today))
    board = ROOT / "LEADERBOARD.md"
    board.write_text(board.read_text().replace(
        "<!-- PARTICIPANTS:END -->", f"| {pid} | {today} |\n<!-- PARTICIPANTS:END -->"))
    print(f"등록 완료: {dest}\n다음: REPORT.md 1절(가설·사전 예상)을 먼저 쓰고, "
          f"python competition/score.py train {dest.relative_to(ROOT.parent)}/submission.py")


if __name__ == "__main__":
    main()
