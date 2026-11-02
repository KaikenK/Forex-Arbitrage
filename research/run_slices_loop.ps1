# Time-stratified GDELT slice download (RESEARCH_TRACKER §6 DEVIATION 3), looped until the
# freeze cutoff 2026-11-10 23:59 IST. Each run re-plans the whole schedule, so failed slices
# are retried before new ones.
#
# Design fixed from pilot throughput/yield only (no headline content read):
#   period 2023-07-01 .. 2025-04-30 (ends with the calendar store, inside HistData coverage),
#   weekdays, 1 h windows ending 07,09,11,13,14,15,17,20 UTC.
#   Query: "currency" if the API accepts CURRENCY_QUERY on a probe, else "default".
#
#   powershell -ExecutionPolicy Bypass -File research\run_slices_loop.ps1
Set-Location (Split-Path $PSScriptRoot -Parent)
# Query rule (tracker 2026-11-02): "currency" if the API accepts data/gdelt.CURRENCY_QUERY on one
# probe (waiting out throttling), else "default". Fixed before any slice content is read.
# A previous choice is reused on restart so the sample never mixes queries.
$choiceFile = 'research/artifacts/gdelt_slices_query_choice.txt'
if (Test-Path $choiceFile) {
    $line = (Get-Content $choiceFile | Select-Object -First 1).TrimStart([char]0xFEFF)   # PS 5.1 writes a BOM
    $query = ($line -replace "^Query choice: '([a-z]+)'.*$", '$1').Trim()
    if ($query -notin @('currency', 'default')) { throw "unreadable query choice: $line" }
} else {
    $query = (python -m research.probe_gdelt_query --max-minutes 90 | Select-Object -Last 1).Trim()
    if ($query -notin @('currency', 'default')) { $query = 'default' }
    "Query choice: '$query' at $(Get-Date -Format o)" | Set-Content -Path $choiceFile -Encoding utf8
}
"Using query '$query'"
$cutoff = Get-Date '2026-11-10 23:59'
while ((Get-Date) -lt $cutoff) {
    python -m research.gdelt_backfill --start 2023-07-01 --end 2025-05-01 --window-hours 1 `
        --slice-hours 7,9,11,13,14,15,17,20 --weekdays-only --query-name $query `
        --min-interval 20 `
        --shard-dir research/artifacts/gdelt_slices_main --max-windows 60 --max-attempts 12 `
        --no-consolidate --log research/artifacts/gdelt_slices_runs.jsonl
    Start-Sleep 5
}
'Cutoff reached; slice loop stopped.'
