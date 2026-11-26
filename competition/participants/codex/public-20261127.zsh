set -eu
zmodload zsh/datetime
export TZ=Asia/Seoul
target=$(/bin/date -j -f '%Y-%m-%d %H:%M:%S' '2026-11-27 00:00:00' '+%s')
while (( (remaining = target - EPOCHSECONDS) > 0 )); do /bin/sleep $remaining; done
[[ $(/bin/date +%F) == 2026-11-27 ]]
folder=competition/participants/codex
name=dividend_turnover_size.py
file=$folder/$name
/usr/bin/grep -Fxq "$name" "$folder/FINAL.txt"
[[ $(/usr/bin/shasum -a 256 "$file") == d85cc52828d25fce2f6d36046558aec3b6f80bbcb48d98e44dee7eb8d398a070* ]] 
if /usr/bin/grep -Eq '^\| codex \|.*d85cc52828' competition/LEADERBOARD.md; then exit 0; fi
result=$(.venv/bin/python -B competition/score.py public "$file")
/usr/bin/sed -i '' "/^## 4. 최종/i\\
2026-11-27 KST 소형주 조합 후속 제출 — ${result//\//\\/}\\
" "$folder/REPORT.md"
