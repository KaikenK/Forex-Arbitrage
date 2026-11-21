#!/usr/bin/env bash
# One-screen health check of the cloud recorder (counts and times only; no outcomes).
DATA=/var/lib/arbex
now=$(date -u +%s)
echo "time (UTC): $(date -u '+%F %T')   (IST: $(TZ=Asia/Kolkata date '+%F %T'))"
for s in arbex-semantic arbex-backend; do
    printf '%-15s %s since %s\n' "$s" "$(systemctl is-active $s)" \
        "$(systemctl show -p ActiveEnterTimestamp --value $s)"
done
day=$(ls -1 $DATA/recordings 2>/dev/null | sort | tail -1)
if [ -n "$day" ] && [ -f "$DATA/recordings/$day/spot.jsonl" ]; then
    f="$DATA/recordings/$day/spot.jsonl"
    age=$(( now - $(stat -c %Y "$f") ))
    echo "recorder:       last write ${age}s ago ($day/spot.jsonl, $(du -h "$f" | cut -f1), $(wc -l < "$f") quotes)"
    [ -f "$DATA/recordings/$day/opportunities.jsonl" ] && \
        echo "opportunities:  $(wc -l < "$DATA/recordings/$day/opportunities.jsonl") today (UTC day $day)"
else
    echo "recorder:       no recording yet"
fi
echo "days recorded:  $(ls -1 $DATA/recordings 2>/dev/null | wc -l)"
echo "shadow rows:    $(cat $DATA/shadow/*/semantic_shadow.jsonl 2>/dev/null | wc -l)"
echo "predictions:    $(wc -l < $DATA/runtime/predictions/live_predictions.jsonl 2>/dev/null || echo 0)"
echo "calendar weeks: $(ls -1 $DATA/cache/calendar/archive 2>/dev/null | tr '\n' ' ')"
echo "disk:           $(df -h $DATA | awk 'NR==2 {print $4 " free of " $2}')"
echo "memory:         $(free -m | awk '/^Mem:/ {print $7 " MB available of " $2}'), swap $(free -m | awk '/^Swap:/ {print $3 " MB used"}'), paging over 5 s: $(vmstat 5 2 | tail -1 | awk '{print "in " $7 " / out " $8 " KB/s"}')"
echo "restarts:       semantic $(systemctl show -p NRestarts --value arbex-semantic), backend $(systemctl show -p NRestarts --value arbex-backend)"
journalctl -u arbex-semantic --no-pager -o cat 2>/dev/null | grep -m1 -E "FinBERT: (ON|OFF)" | sed 's/^/semantic:       /'
