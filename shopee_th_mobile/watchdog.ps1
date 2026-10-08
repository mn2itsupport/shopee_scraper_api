# Keeps shopee_th_mobile alive: the phone Chrome (launch_chrome.ps1, CDP on
# 127.0.0.1:9223) and the mobile agent (agent.py on 127.0.0.1:8766). Same
# idea as scripts\price_agent_watchdog.ps1 but fully separate — own mutex,
# own log — so the two can run side by side.
#
#   powershell -ExecutionPolicy Bypass -WindowStyle Hidden -File shopee_th_mobile\watchdog.ps1
#
# Log: logs\shopee_th_mobile_watchdog.log. Never logs into Shopee or touches
# captchas — if the session expires, log in again by hand in that Chrome.

param(
    [int]$IntervalSeconds = 60,
    [int]$AgentPort = 8766,
    [string]$CdpUrl = "http://127.0.0.1:9223"
)

$repo = Split-Path $PSScriptRoot -Parent
$logDir = Join-Path $repo "logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir "shopee_th_mobile_watchdog.log"

function Write-Log([string]$msg) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg" | Out-File -FilePath $log -Append -Encoding utf8
}

$mutex = New-Object System.Threading.Mutex($false, "Global\shopee_th_mobile_watchdog")
if (-not $mutex.WaitOne(0)) {
    Write-Log "another mobile watchdog is already running, exiting"
    exit 0
}

$python = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
Write-Log "watchdog started (python=$python, interval=${IntervalSeconds}s)"

function Test-Url([string]$url) {
    try {
        Invoke-RestMethod $url -TimeoutSec 5 | Out-Null
        return $true
    } catch {
        return $false
    }
}

while ($true) {
    if (-not (Test-Url "$CdpUrl/json/version")) {
        Write-Log "phone Chrome not answering at $CdpUrl, launching it"
        & powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "launch_chrome.ps1") | Out-Null
        Start-Sleep -Seconds 10
        if (Test-Url "$CdpUrl/json/version") { Write-Log "phone Chrome is up" } else { Write-Log "phone Chrome still not answering" }
    }

    if (-not (Test-Url "http://127.0.0.1:$AgentPort/health")) {
        Write-Log "mobile agent not answering on port $AgentPort, starting it"
        Start-Process -FilePath $python -WorkingDirectory $repo -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $logDir "shopee_th_mobile_agent.out.log") `
            -RedirectStandardError (Join-Path $logDir "shopee_th_mobile_agent.err.log") `
            -ArgumentList @("-m", "uvicorn", "shopee_th_mobile.agent:app", "--host", "127.0.0.1", "--port", "$AgentPort")
        Start-Sleep -Seconds 10
        if (Test-Url "http://127.0.0.1:$AgentPort/health") { Write-Log "mobile agent is up" } else { Write-Log "mobile agent still not answering, see logs\shopee_th_mobile_agent.err.log" }
    }

    Start-Sleep -Seconds $IntervalSeconds
}
