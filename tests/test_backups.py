"""Tests for the backup retention rule and the backup/sync round trip."""

import sqlite3

import backups


class TestToPrune:
    def test_keeps_newest_and_first_of_each_month(self):
        dates = ["2026-09-01", "2026-09-02", "2026-09-30",
                 "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07"]

        # keep 2 newest (10-06, 10-07) + first of Sep (09-01) and Oct (10-02)
        assert backups.to_prune(dates, keep_daily=2) == [
            "2026-09-02", "2026-09-30", "2026-10-05"]

    def test_first_of_month_need_not_be_the_1st(self):
        # no backup on a weekend 1st - the month's first one is kept
        assert backups.to_prune(["2026-11-02", "2026-11-03"], keep_daily=0) == [
            "2026-11-03"]

    def test_nothing_to_prune_when_few(self):
        assert backups.to_prune(["2026-10-21", "2026-10-22"]) == []


class TestRunAndSync:
    def test_backup_copies_rows_and_sync_fills_a_new_dir(self, tmp_path, monkeypatch):
        db = tmp_path / "quant.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE t (x)")
        conn.executemany("INSERT INTO t VALUES (?)", [(1,), (2,)])
        conn.commit()
        conn.close()
        local = tmp_path / "backups"
        monkeypatch.setattr(backups, "DATABASES", [db, tmp_path / "missing.db"])
        monkeypatch.setattr(backups, "BACKUP_DIRS", [local])

        [path] = backups.run("2026-10-23")

        assert path == local / "2026-10-23" / "quant.db"
        assert sqlite3.connect(path).execute("SELECT COUNT(*) FROM t").fetchone() == (2,)

        cloud = tmp_path / "cloud"
        assert backups.sync(cloud) == 1
        assert backups.sync(cloud) == 0
        assert (cloud / "2026-10-23" / "quant.db").exists()
