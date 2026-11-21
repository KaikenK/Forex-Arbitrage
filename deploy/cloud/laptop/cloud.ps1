# Talk to the Arbex cloud recorder from the laptop (Windows OpenSSH, key ~\.ssh\arbex_azure).
#   cloud.ps1 status   one-screen health check (counts and times only)
#   cloud.ps1 pull     copy new recordings, shadow rows, prediction log and calendar archive to
#                      C:\Users\xyreo\arbex\cloud_data (finished days once, the last two days again)
#   cloud.ps1 tunnel   private view of the cloud live monitor at http://localhost:18000/live
#   cloud.ps1 logs     last service log lines
#   cloud.ps1 ssh      open a shell on the server
param([ValidateSet('status', 'pull', 'tunnel', 'logs', 'ssh')][string]$Action = 'status')
$ErrorActionPreference = 'Stop'
$HostFile = Join-Path $PSScriptRoot 'host.txt'
if (-not (Test-Path $HostFile)) { throw "No host.txt: the cloud server has not been set up yet." }
$Target = (Get-Content $HostFile -Raw).Trim()                 # e.g. azureuser@20.1.2.3
$Key = Join-Path $env:USERPROFILE '.ssh\arbex_azure'
$Opts = @('-i', $Key, '-o', 'StrictHostKeyChecking=accept-new', '-o', 'ServerAliveInterval=30', '-o', 'ConnectTimeout=20')
$Ssh = 'C:\Windows\System32\OpenSSH\ssh.exe'
$Scp = 'C:\Windows\System32\OpenSSH\scp.exe'
$Remote = '/var/lib/arbex'
$Local = 'C:\Users\xyreo\arbex\cloud_data'

function Pull-Days([string]$Sub) {
    $dest = Join-Path $Local $Sub
    New-Item -ItemType Directory -Force $dest | Out-Null
    $days = & $Ssh @Opts $Target "ls -1 $Remote/$Sub 2>/dev/null"
    $recent = (Get-Date).ToUniversalTime().AddDays(-1).ToString('yyyy-MM-dd')
    foreach ($day in $days) {
        $day = $day.Trim(); if (-not $day) { continue }
        if ((Test-Path (Join-Path $dest $day)) -and ($day -lt $recent)) { continue }   # finished day already here
        Write-Host "  $Sub/$day"
        & $Scp @Opts -C -p -r -q "${Target}:$Remote/$Sub/$day" "$dest\"
    }
}

switch ($Action) {
    'status' { & $Ssh @Opts $Target 'arbex-status' }
    'logs'   { & $Ssh @Opts $Target 'journalctl -u arbex-backend -u arbex-semantic -n 60 --no-pager -o short-iso' }
    'ssh'    { & $Ssh @Opts $Target }
    'tunnel' {
        Write-Host 'Cloud live monitor: http://localhost:18000/live   (close this window to stop)'
        & $Ssh @Opts -N -L 18000:127.0.0.1:8000 -L 19000:127.0.0.1:9000 $Target
    }
    'pull' {
        Write-Host "Pulling into $Local"
        Pull-Days 'recordings'
        Pull-Days 'shadow'
        New-Item -ItemType Directory -Force (Join-Path $Local 'predictions'), (Join-Path $Local 'calendar') | Out-Null
        & $Scp @Opts -C -p -q "${Target}:$Remote/runtime/predictions/live_predictions.jsonl" (Join-Path $Local 'predictions\')
        & $Scp @Opts -C -p -r -q "${Target}:$Remote/cache/calendar/archive" (Join-Path $Local 'calendar\')
        Write-Host 'Done.'
    }
}
