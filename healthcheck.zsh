#!/bin/zsh
# Unattended health check for the quant-lab launchd jobs.
#
# Deliberately not Python: on 2026-10-06 a Homebrew Python upgrade made every
# launchd job die at spawn (SIGKILL, no log line) - a checker running on the
# same .venv/bin/python would have died with them. This uses only zsh and
# system binaries (sqlite3, launchctl, plutil, ps, osascript, perl).
#
# Checks (research/pbr.md section 26/27 for background):
#   1. python boot  - every interpreter the configured jobs launch runs
#                     `-c pass` and exits 0 (bounded by a timeout)
#   2. launchctl    - each job is loaded, its last exit code is 0, and it
#                     isn't still running past QL_MAX_RUN_SECS (a hang).
#                     A job stuck at spawn (OS_REASON_CODESIGNING) is
#                     re-registered (bootout + bootstrap) and reported as
#                     healed, not as a problem, when that clears it
#   3. daily        - weekdays from 16:00: today's daily.py run is in
#                     cron.log/bot.log, and a runs row exists if the market
#                     was open
#   4. prices       - weekdays from 12:45: pead_price_raw's latest date is the
#                     last trading day before today (runs is the calendar)
#                     and that date covers >= 85% of tracked symbols
# Problems go to logs/health.log and a desktop notification (each problem
# at most once a day). A heartbeat file is written every run, problems or
# not; daily.py warns when it goes stale, so a dead checker gets noticed.
# Exit 0 when healthy, 1 otherwise.
#
# Every path, the clock and the external commands can be overridden with
# QL_* variables - tests/test_healthcheck.py runs it against fakes.

emulate -L zsh
setopt pipe_fail

ROOT=${QL_ROOT:-${0:A:h}}
DB=${QL_DB:-$ROOT/data/quant.db}
LOG_DIR=${QL_LOG_DIR:-$ROOT/logs}
STATE_DIR=${QL_STATE_DIR:-$LOG_DIR}
PLIST_DIR=${QL_PLIST_DIR:-$HOME/Library/LaunchAgents}
LAUNCHCTL=${QL_LAUNCHCTL:-/bin/launchctl}
SQLITE=${QL_SQLITE:-/usr/bin/sqlite3}
LABELS=(${=QL_LABELS:-daily pricerefresh checkdue amzncheck pbrcheck tossmorning disclosureflag})
TODAY=${QL_TODAY:-$(date +%F)}
NOW=${QL_NOW:-$(date +%H%M)}
MAX_RUN_SECS=${QL_MAX_RUN_SECS:-1800}
PY_TIMEOUT=${QL_PY_TIMEOUT:-20}
PREFIX=com.jongminpark.quantlab
DOMAIN=gui/$(id -u)

mkdir -p "$LOG_DIR" "$STATE_DIR"
HEALTH_LOG=$LOG_DIR/health.log
ALERTED=$STATE_DIR/healthcheck.alerted
HEARTBEAT=$STATE_DIR/healthcheck.heartbeat

problems=()
add() { problems+=("$1") }

notify() {  # notify TITLE MESSAGE [PHONE_TEXT]
    if [[ -n ${QL_NOTIFY:-} ]]; then
        "$QL_NOTIFY" "$1" "$2" "${3:-}"
        return
    fi
    local t=${1//\\/\\\\} m=${2//\\/\\\\}
    t=${t//\"/\\\"}; m=${m//\"/\\\"}
    /usr/bin/osascript -e "display notification \"$m\" with title \"$t\"" >/dev/null 2>&1
    # Phone copy over iMessage, same as quant/notify.py (IMESSAGE_TO in .env).
    local to=$(/usr/bin/sed -n 's/^IMESSAGE_TO=//p' "$ROOT/.env" 2>/dev/null | /usr/bin/tr -d "\"'" | /usr/bin/head -1)
    if [[ -n $to ]]; then
        local ph=${3:-$2}
        ph=${ph//\\/\\\\}; ph=${ph//\"/\\\"}
        /usr/bin/osascript -e "tell application \"Messages\" to send \"[$t] $ph\" to participant \"$to\" of (1st account whose service type = iMessage)" >/dev/null 2>&1
    fi
}

phone_text() {  # phone_text PROBLEM... -> short Korean lines for the iMessage copy
    local -aU spawn failed hung unloaded other fixed
    local daily=0 prices=0 boot=0 p label
    for p in "$@"; do
        label=${p%%:*}
        case $p in
            "healed "*) fixed+=(${p#healed }) ;;
            *"spawn failed"*|*OS_REASON_CODESIGNING*|*"terminating signal"*) spawn+=($label) ;;
            *"last exit code"*) failed+=($label) ;;
            *"still running"*) hung+=($label) ;;
            *"not loaded"*|*"plist missing"*) unloaded+=($label) ;;
            "python boot failed"*) boot=1 ;;
            "daily: no run recorded"*|"daily: ran but no runs row"*) daily=1 ;;
            prices:*) prices=1 ;;
            *) other+=("${p[1,50]}") ;;
        esac
    done
    local out="헬스체크 이상"
    (( ${#fixed} && ${#fixed} == $# )) && out="헬스체크 자동 복구"
    (( ${#fixed} )) && out+=$'\n'"• 시작 실패 작업 자동 재등록함: ${(j:, :)fixed} (이미 놓친 실행은 다시 안 돌림)"
    (( boot )) && out+=$'\n'"• 파이썬 실행 실패 (모든 작업 영향)"
    (( ${#spawn} )) && out+=$'\n'"• 자동 작업 시작 실패: ${(j:, :)spawn} (자동 재등록으로 안 고쳐짐 → 확인 필요)"
    (( ${#failed} )) && out+=$'\n'"• 작업 오류 종료: ${(j:, :)failed}"
    (( ${#hung} )) && out+=$'\n'"• 작업이 멈춘 듯: ${(j:, :)hung}"
    (( ${#unloaded} )) && out+=$'\n'"• 작업 미등록: ${(j:, :)unloaded}"
    (( daily )) && out+=$'\n'"• 오늘 daily 기록 없음"
    (( prices )) && out+=$'\n'"• 가격 데이터 갱신 안 됨"
    for p in $other; do out+=$'\n'"• $p"; done
    print -r -- "$out"
}

etime_secs() {  # ps etime "[[dd-]hh:]mm:ss" -> seconds
    local e=${1// /} d=0 h=0 m=0 s=0
    [[ $e == *-* ]] && { d=${e%%-*}; e=${e#*-} }
    local parts=(${(s.:.)e})
    case ${#parts} in
        3) h=${parts[1]}; m=${parts[2]}; s=${parts[3]} ;;
        2) m=${parts[1]}; s=${parts[2]} ;;
        1) s=${parts[1]} ;;
    esac
    print $(( 10#$d * 86400 + 10#$h * 3600 + 10#$m * 60 + 10#$s ))
}

sql() { "$SQLITE" "$DB" "$1" 2>/dev/null }

weekday=$(date -j -f %F "$TODAY" +%u 2>/dev/null)   # 1=Mon .. 7=Sun

# 1. python boot - every distinct interpreter the configured jobs launch.
typeset -A seen_prog
for label in $LABELS; do
    plist=$PLIST_DIR/$PREFIX.$label.plist
    if [[ ! -f $plist ]]; then
        add "$label: plist missing ($plist)"
        continue
    fi
    prog=$(/usr/bin/plutil -extract ProgramArguments.0 raw -o - "$plist" 2>/dev/null)
    [[ -z $prog || -n ${seen_prog[$prog]:-} ]] && continue
    seen_prog[$prog]=1
    /usr/bin/perl -e 'alarm shift; exec @ARGV or exit 127' "$PY_TIMEOUT" "$prog" -c pass >/dev/null 2>&1
    rc=$?
    (( rc != 0 )) && add "python boot failed: $prog -c pass exited $rc"
done

codesign_stuck() {  # codesign_stuck PRINT_OUTPUT -> 0 if the job is stuck at spawn
    print -r -- "$1" | grep -qE $'^\t(last exit reason = OS_REASON_CODESIGNING|job state = spawn failed)$'
}

# A job stuck at spawn with OS_REASON_CODESIGNING stays stuck until it is
# re-registered (seen 2026-10-06, 11-16, 11-25 - after reboots/OS updates and
# also without one). Re-registering is safe: none of LABELS has RunAtLoad, so
# it only reloads the schedule and runs nothing. Skipped when an interpreter
# fails to boot - that is a broken Python, which re-registering can't fix.
healed=()
python_ok=1
for p in $problems; do [[ $p == "python boot failed"* ]] && python_ok=0; done

# 2. launchctl - loaded, last exit 0, not hung.
for label in $LABELS; do
    out=$("$LAUNCHCTL" print "$DOMAIN/$PREFIX.$label" 2>/dev/null)
    if (( $? != 0 )) || [[ -z $out ]]; then
        add "$label: not loaded in launchd"
        continue
    fi
    if (( python_ok )) && codesign_stuck "$out" \
       && [[ -f $PLIST_DIR/$PREFIX.$label.plist ]] \
       && ! print -r -- "$out" | grep -qx $'\tstate = running'; then
        "$LAUNCHCTL" bootout "$DOMAIN/$PREFIX.$label" >/dev/null 2>&1
        "$LAUNCHCTL" bootstrap "$DOMAIN" "$PLIST_DIR/$PREFIX.$label.plist" >/dev/null 2>&1
        after=$("$LAUNCHCTL" print "$DOMAIN/$PREFIX.$label" 2>/dev/null)
        if (( $? == 0 )) && [[ -n $after ]] && ! codesign_stuck "$after"; then
            healed+=($label)
            continue
        fi
        add "$label: re-register did not fix spawn failure"
        [[ -z $after ]] && { add "$label: not loaded in launchd"; continue }
        out=$after
    fi
    last=$(print -r -- "$out" | sed -n $'s/^\tlast exit code = //p' | head -1)
    if [[ -n $last && $last != 0 && $last != "(never exited)" ]]; then
        add "$label: last exit code $last"
    fi
    # A job killed at spawn has no exit code at all - on 2026-10-06 pbrcheck
    # showed only "last exit reason = OS_REASON_CODESIGNING" and
    # "job state = spawn failed" (launchctl list: -9).
    reason=$(print -r -- "$out" | sed -n $'s/^\tlast exit reason = //p' | head -1)
    [[ -n $reason ]] && add "$label: last exit reason $reason"
    signal=$(print -r -- "$out" | sed -n $'s/^\tlast terminating signal = //p' | head -1)
    [[ -n $signal ]] && add "$label: last terminating signal $signal"
    jstate=$(print -r -- "$out" | sed -n $'s/^\tjob state = //p' | head -1)
    [[ $jstate == "spawn failed" ]] && add "$label: job state spawn failed"
    state=$(print -r -- "$out" | sed -n $'s/^\tstate = //p' | head -1)
    if [[ $state == running ]]; then
        pid=$(print -r -- "$out" | sed -n $'s/^\tpid = //p' | head -1)
        if [[ -n $pid ]]; then
            et=$(ps -o etime= -p "$pid" 2>/dev/null)
            if [[ -n $et ]]; then
                secs=$(etime_secs "$et")
                (( secs > MAX_RUN_SECS )) && add "$label: still running after ${secs}s (pid $pid) - hung?"
            fi
        fi
    fi
done

if [[ -n $weekday ]] && (( weekday <= 5 )); then
    # 3. daily - after its 15:40 slot.
    if (( 10#$NOW >= 1600 )); then
        line=$(cat "$LOG_DIR/cron.log" "$LOG_DIR/bot.log" 2>/dev/null \
               | grep -E "^$TODAY .*daily: (daily run done|market closed today)" | tail -1)
        if [[ -z $line ]]; then
            add "daily: no run recorded for $TODAY"
        elif [[ $line == *"daily run done"* ]]; then
            n=$(sql "SELECT COUNT(*) FROM runs WHERE trade_date = '$TODAY'")
            [[ $n == 0 ]] && add "daily: ran but no runs row for $TODAY"
        fi
    fi

    # 4. prices - after the 09:30 refresh (moved from 12:30 on 2026-10-15).
    if (( 10#$NOW >= 1245 )); then
        expected=$(sql "SELECT REPLACE(MAX(trade_date), '-', '') FROM runs WHERE trade_date < '$TODAY'")
        latest=$(sql "SELECT MAX(date) FROM pead_price_raw")
        if [[ -z $latest ]]; then
            add "prices: pead_price_raw unreadable or empty"
        else
            if [[ -n $expected && $latest < $expected ]]; then
                add "prices: latest $latest, expected $expected"
            fi
            n=$(sql "SELECT COUNT(*) FROM pead_price_raw WHERE date = '$latest'")
            tracked=$(sql "SELECT COUNT(*) FROM pead_price_fetch_state")
            if [[ -n $n && -n $tracked ]] && (( tracked > 0 && n * 100 < tracked * 85 )); then
                add "prices: $latest has $n of $tracked symbols (partial sync)"
            fi
        fi
    fi
fi

stamp=$(date "+%Y-%m-%d %H:%M:%S")
new=()
for p in ${healed/#/healed } $problems; do
    key="$TODAY $p"
    if ! grep -qxF -- "$key" "$ALERTED" 2>/dev/null; then
        print -r -- "$key" >> "$ALERTED"
        new+=("$p")
    fi
done
for l in $healed; do print -r -- "$stamp HEALED $l: re-registered after spawn failure" >> "$HEALTH_LOG"; done
if (( ${#problems} )); then
    for p in $problems; do print -r -- "$stamp PROBLEM $p" >> "$HEALTH_LOG"; done
else
    print -r -- "$stamp OK" >> "$HEALTH_LOG"
fi
(( ${#new} )) && notify "quant-lab health" "${(j:; :)new}" "$(phone_text "${new[@]}")"

date +%s > "$HEARTBEAT"
(( ${#problems} == 0 ))
