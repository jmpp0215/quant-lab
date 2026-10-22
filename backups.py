"""Daily database backups.

    python backups.py                  back up now (daily.py does this after the close)
    python backups.py --sync <dir>     copy every local backup missing from <dir>

The databases are edited by hand now and then (cashflows, corrected
snapshots), and nothing else keeps an old copy. Each backup is a folder
data/backups/YYYY-MM-DD/ holding quant.db and pbr_live.db, written with
SQLite's online backup API so a copy taken while daily.py or the tracker
has a connection open is still consistent.

BACKUP_DIRS lists where backups go. Only the local folder today; adding a
second (e.g. iCloud Drive) later and running --sync <that dir> once
brings over every backup taken before it existed.
"""

import logging
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent
DATABASES = [ROOT / "data" / "quant.db", ROOT / "data" / "pbr_live.db"]
BACKUP_DIRS = [ROOT / "data" / "backups"]
KEEP_DAILY = 14   # most recent backups kept regardless of date

log = logging.getLogger("backups")


def to_prune(dates: list[str], keep_daily: int = KEEP_DAILY) -> list[str]:
    """Backup dates ("YYYY-MM-DD") to delete: all but the newest
    `keep_daily` and the first backup of each month. Pure."""
    dates = sorted(dates)
    keep = set(dates[-keep_daily:]) if keep_daily else set()
    first_of_month: dict[str, str] = {}
    for d in dates:
        first_of_month.setdefault(d[:7], d)
    keep |= set(first_of_month.values())
    return [d for d in dates if d not in keep]


def _dated_folders(base: Path) -> list[str]:
    if not base.exists():
        return []
    return sorted(p.name for p in base.iterdir()
                  if p.is_dir() and len(p.name) == 10 and p.name[4] == "-")


def run(today: str) -> list[Path]:
    """Back up every database into each BACKUP_DIRS/today/, then prune."""
    written = []
    for base in BACKUP_DIRS:
        folder = base / today
        folder.mkdir(parents=True, exist_ok=True)
        for db in DATABASES:
            if not db.exists():
                continue
            src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            dst = sqlite3.connect(folder / db.name)
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
            written.append(folder / db.name)
        for old in to_prune(_dated_folders(base)):
            shutil.rmtree(base / old)
            log.info("pruned backup %s", base / old)
    log.info("backed up %d files for %s", len(written), today)
    return written


def sync(target: Path) -> int:
    """Copy local backup folders that `target` doesn't have yet."""
    source = BACKUP_DIRS[0]
    copied = 0
    for name in _dated_folders(source):
        if not (target / name).exists():
            shutil.copytree(source / name, target / name)
            copied += 1
    return copied


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if "--sync" in sys.argv:
        target = Path(sys.argv[sys.argv.index("--sync") + 1]).expanduser()
        print(f"copied {sync(target)} backup folders to {target}")
        return 0
    run(datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat())
    return 0


if __name__ == "__main__":
    sys.exit(main())
