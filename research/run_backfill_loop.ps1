# Short GDELT backfill runs in a loop until 2026-11-04 00:00 IST (owner's request). The
# freeze only admits shards completed before its own cutoff, so later downloads cannot leak in.
# Each run re-plans from 2018, so any day that failed (throttling, Wi-Fi drop) is retried
# before new days are fetched. Run from anywhere:
#   powershell -ExecutionPolicy Bypass -File research\run_backfill_loop.ps1
Set-Location (Split-Path $PSScriptRoot -Parent)
$cutoff = Get-Date '2026-11-04 00:00'
while ((Get-Date) -lt $cutoff) {
    python -m research.gdelt_backfill --start 2018-01-01 --end 2022-01-01 --max-attempts 12 --max-windows 30 --no-consolidate
    Start-Sleep 5
}
'Cutoff reached; backfill loop stopped.'
