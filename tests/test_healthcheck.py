"""healthcheck.zsh, run for real against fakes: a fake launchctl that prints
canned `launchctl print` output, fake interpreters, a temp DB and logs, and a
QL_NOTIFY command that records notifications instead of showing them."""
import os
import plistlib
import sqlite3
import stat
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "healthcheck.zsh"
PREFIX = "com.jongminpark.quantlab"
LABELS = ["daily", "pricerefresh"]


def launchctl_print(state="not running", last="0", pid=None, extra=()):
    lines = ["gui/501/x = {", "\tactive count = 0", f"\tstate = {state}", "",
             "\tprogram = /x/python", "\targuments = {", "\t\tlast exit code = 99", "\t}"]
    if pid is not None:
        lines.append(f"\tpid = {pid}")
    lines.append("\truns = 3")
    if last is not None:
        lines.append(f"\tlast exit code = {last}")
    lines += [*extra, "}"]
    return "\n".join(lines) + "\n"


def executable(path: Path, body: str) -> Path:
    path.write_text("#!/bin/zsh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


class Env:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.logs = tmp / "logs"
        self.logs.mkdir()
        self.plists = tmp / "plists"
        self.plists.mkdir()
        self.lc = tmp / "lc"
        self.lc.mkdir()
        self.notes = tmp / "notes.txt"
        self.db = tmp / "quant.db"
        self.python_ok = executable(tmp / "python_ok", "exit 0\n")
        self.python_bad = executable(tmp / "python_bad", "exit 9\n")
        executable(tmp / "launchctl",
                   'f="$FAKE_LC/${2##*/}"; [[ -f $f ]] || exit 113; cat "$f"\n')
        executable(tmp / "notify", f'print -r -- "$1|$2" >> "{self.notes}"\n')
        conn = sqlite3.connect(self.db)
        conn.executescript("""
            CREATE TABLE runs (trade_date TEXT PRIMARY KEY, updated_at TEXT, session TEXT, ok INTEGER);
            CREATE TABLE pead_price_raw (date TEXT, symbol TEXT, close REAL, PRIMARY KEY (date, symbol));
            CREATE TABLE pead_price_fetch_state (symbol TEXT PRIMARY KEY, fetched_to TEXT, updated_at TEXT);
        """)
        conn.close()
        for label in LABELS:
            self.plist(label, self.python_ok)
            self.job(label)

    def plist(self, label, python):
        with open(self.plists / f"{PREFIX}.{label}.plist", "wb") as f:
            plistlib.dump({"Label": f"{PREFIX}.{label}",
                           "ProgramArguments": [str(python), "x.py"]}, f)

    def job(self, label, **kw):
        (self.lc / f"{PREFIX}.{label}").write_text(launchctl_print(**kw))

    def unload(self, label):
        (self.lc / f"{PREFIX}.{label}").unlink()

    def sql(self, *stmts):
        conn = sqlite3.connect(self.db)
        for s in stmts:
            conn.execute(s)
        conn.commit()
        conn.close()

    def prices(self, date, n_symbols, tracked=10):
        self.sql(*[f"INSERT OR REPLACE INTO pead_price_raw VALUES ('{date}', 'S{i}', 1)"
                   for i in range(n_symbols)],
                 *[f"INSERT OR REPLACE INTO pead_price_fetch_state VALUES ('S{i}', 'x', 'x')"
                   for i in range(tracked)])

    def daily_log(self, line, file="cron.log"):
        with open(self.logs / file, "a") as f:
            f.write(line + "\n")

    def run(self, today="2026-10-07", now="1630", **extra):
        env = {**os.environ, "QL_ROOT": str(self.tmp), "QL_DB": str(self.db),
               "QL_LOG_DIR": str(self.logs), "QL_PLIST_DIR": str(self.plists),
               "QL_LAUNCHCTL": str(self.tmp / "launchctl"), "FAKE_LC": str(self.lc),
               "QL_NOTIFY": str(self.tmp / "notify"), "QL_LABELS": " ".join(LABELS),
               "QL_TODAY": today, "QL_NOW": now, **extra}
        env = {k: v for k, v in env.items() if v is not None}  # None = leave unset
        r = subprocess.run(["/bin/zsh", str(SCRIPT)], env=env, capture_output=True, text=True,
                           timeout=60)
        return r.returncode

    def notes_text(self):
        return self.notes.read_text() if self.notes.exists() else ""

    def health(self):
        return (self.logs / "health.log").read_text()


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    # A healthy Wednesday 2026-10-07 after the daily slot: yesterday's
    # prices in, full coverage, today's daily logged and recorded.
    e.sql("INSERT INTO runs VALUES ('2026-10-06', 'x', 'afterMarket', 1)",
          "INSERT INTO runs VALUES ('2026-10-07', 'x', 'afterMarket', 1)")
    e.prices("20261006", 10)
    e.daily_log("2026-10-07 15:41:00 INFO     daily: daily run done")
    return e


class TestHealthy:
    def test_all_clear_exits_zero_without_a_notification(self, env):
        assert env.run() == 0
        assert env.notes_text() == ""
        assert env.health().strip().endswith("OK")

    def test_heartbeat_is_written(self, env):
        env.run()
        beat = int((env.logs / "healthcheck.heartbeat").read_text())
        assert abs(beat - time.time()) < 60


class TestLaunchd:
    def test_sigkilled_job_is_reported(self, env):
        # 2026-10-06: every job launched after the Python upgrade died -9.
        env.job("daily", last="-9")
        assert env.run() == 1
        assert "daily: last exit code -9" in env.notes_text()

    def test_spawn_failure_without_exit_code_is_reported(self, env):
        # What pbrcheck actually showed on 2026-10-06: no "last exit code"
        # line at all, only an exit reason and a spawn-failed job state.
        env.job("daily", last=None, extra=("\tlast exit reason = OS_REASON_CODESIGNING",
                                           "\tjob state = spawn failed"))
        assert env.run() == 1
        notes = env.notes_text()
        assert "daily: last exit reason OS_REASON_CODESIGNING" in notes
        assert "daily: job state spawn failed" in notes

    def test_terminating_signal_is_reported(self, env):
        env.job("daily", last=None, extra=("\tlast terminating signal = Killed: 9",))
        assert env.run() == 1
        assert "daily: last terminating signal Killed: 9" in env.notes_text()

    def test_unloaded_job_is_reported(self, env):
        env.unload("pricerefresh")
        assert env.run() == 1
        assert "pricerefresh: not loaded" in env.notes_text()

    def test_nested_exit_code_lines_are_ignored(self, env):
        # Only the top-level (one-tab) "last exit code" counts; the fake
        # output carries a nested "\t\tlast exit code = 99" decoy.
        assert env.run() == 0

    def test_long_running_job_is_reported_as_hung(self, env):
        # 2026-10-05: refresh_prices.py sat in a request for 22 hours.
        sleeper = subprocess.Popen(["/bin/sleep", "30"])
        try:
            time.sleep(1.2)
            env.job("pricerefresh", state="running", last="(never exited)", pid=sleeper.pid)
            assert env.run(QL_MAX_RUN_SECS="0") == 1
            assert "pricerefresh: still running" in env.notes_text()
        finally:
            sleeper.kill()

    def test_running_job_within_limit_is_fine(self, env):
        sleeper = subprocess.Popen(["/bin/sleep", "30"])
        try:
            env.job("pricerefresh", state="running", last="(never exited)", pid=sleeper.pid)
            assert env.run() == 0
        finally:
            sleeper.kill()


class TestPythonBoot:
    def test_interpreter_that_fails_to_start_is_reported(self, env):
        env.plist("daily", env.python_bad)
        assert env.run() == 1
        assert "python boot failed" in env.notes_text()

    def test_missing_plist_is_reported(self, env):
        (env.plists / f"{PREFIX}.daily.plist").unlink()
        assert env.run() == 1
        assert "daily: plist missing" in env.notes_text()


class TestDaily:
    def test_missing_daily_run_is_reported_after_1600(self, env):
        (env.logs / "cron.log").write_text("")
        assert env.run() == 1
        assert "daily: no run recorded for 2026-10-07" in env.notes_text()

    def test_daily_not_checked_before_its_slot(self, env):
        (env.logs / "cron.log").write_text("")
        assert env.run(now="1315") == 0

    def test_manual_run_in_bot_log_counts(self, env):
        # 2026-10-06's daily was filled by hand; it logs to bot.log only.
        (env.logs / "cron.log").write_text("")
        env.daily_log("2026-10-07 16:22:41 INFO     daily: daily run done", file="bot.log")
        assert env.run() == 0

    def test_market_closed_needs_no_runs_row(self, env):
        (env.logs / "cron.log").write_text(
            "2026-10-07 15:52:59 INFO     daily: market closed today; nothing to record\n")
        env.sql("DELETE FROM runs WHERE trade_date = '2026-10-07'")
        assert env.run() == 0

    def test_run_logged_but_no_runs_row_is_reported(self, env):
        env.sql("DELETE FROM runs WHERE trade_date = '2026-10-07'")
        assert env.run() == 1
        assert "no runs row" in env.notes_text()

    def test_weekend_skips_daily_and_price_checks(self, env):
        (env.logs / "cron.log").write_text("")
        env.sql("DELETE FROM pead_price_raw")
        assert env.run(today="2026-10-10") == 0  # Saturday


class TestPrices:
    def test_stale_prices_are_reported(self, env):
        env.sql("DELETE FROM pead_price_raw WHERE date = '20261006'")
        env.prices("20261002", 10)
        assert env.run() == 1
        assert "prices: latest 20261002, expected 20261006" in env.notes_text()

    def test_partial_sync_is_reported(self, env):
        # 2026-10-02 ended up with 46 of 943 symbols.
        env.sql("DELETE FROM pead_price_raw WHERE date = '20261006'")
        env.prices("20261006", 4)
        assert env.run() == 1
        assert "4 of 10 symbols" in env.notes_text()

    def test_prices_not_checked_before_the_refresh(self, env):
        env.sql("DELETE FROM pead_price_raw WHERE date = '20261006'")
        env.prices("20261002", 10)
        assert env.run(now="1200") == 0


class TestAlerting:
    def test_same_problem_notifies_once_a_day(self, env):
        env.job("daily", last="-9")
        env.run()
        env.run()
        assert env.notes_text().count("last exit code -9") == 1
        assert env.health().count("PROBLEM daily: last exit code -9") == 2

    def test_same_problem_notifies_again_the_next_day(self, env):
        env.job("daily", last="-9")
        env.run(today="2026-10-07")
        env.run(today="2026-10-08", now="1200")
        assert env.notes_text().count("last exit code -9") == 2


class TestDefaultLabels:
    def test_pbrcheck_is_checked_by_default(self, env):
        # Reloaded 2026-10-06 after being held (RESEARCH_LOG section 27); it
        # auto-runs run_pbr_live.py on a due day, so it must be watched.
        for label in ["checkdue", "amzncheck"]:
            env.plist(label, env.python_ok)
            env.job(label)
        assert env.run(QL_LABELS=None) == 1
        notes = env.notes_text()
        assert "pbrcheck: plist missing" in notes
        assert "checkdue" not in notes and "amzncheck" not in notes

    def test_all_default_jobs_healthy(self, env):
        for label in ["checkdue", "amzncheck", "pbrcheck"]:
            env.plist(label, env.python_ok)
            env.job(label)
        assert env.run(QL_LABELS=None) == 0
