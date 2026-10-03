# Keeps the shopee_th Chrome machine side alive: the debug Chrome
# (scripts/launch_real_chrome_th.ps1, CDP on 127.0.0.1:9222) and the price
# agent (scripts/price_agent.py on 127.0.0.1:8765). Checks both every
# $IntervalSeconds and restarts whichever stopped answering. Meant to be
# started at logon by Task Scheduler (scripts/install_price_agent_task.ps1);
# Chrome needs a logged-in desktop session, so this can't run as a service.
#
#   powershell -ExecutionPolicy Bypass -WindowStyle Hidden -File scripts\price_agent_watchdog.ps1
#
# Log: logs/price_agent_watchdog.log. Does not log into Shopee or touch
# captchas — if the session expires, log in again by hand in that Chrome.

param(
    [int]$IntervalSeconds = 60,
    [int]$AgentPort = 8765,
    [string]$CdpUrl = "http://127.0.0.1:9222"
)

$repo = Split-Path $PSScriptRoot -Parent
$logDir = Join-Path $repo "logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir "price_agent_watchdog.log"

function Write-Log([string]$msg) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg" | Out-File -FilePath $log -Append -Encoding utf8
}

# Only one watchdog at a time (e.g. logon task + a manual start).
$mutex = New-Object System.Threading.Mutex($false, "Global\shopee_th_price_agent_watchdog")
if (-not $mutex.WaitOne(0)) {
    Write-Log "another watchdog is already running, exiting"
    exit 0
}

# Prefer the project venv when it actually has the dependencies, otherwise
# fall back to whatever `python` is on PATH.
$python = "python"
$venvPython = Join-Path $repo ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    & $venvPython -c "import fastapi, playwright, uvicorn" 2>$null
    if ($LASTEXITCODE -eq 0) { $python = $venvPython }
}
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
        Write-Log "Chrome CDP not answering at $CdpUrl, launching Chrome"
        & powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "launch_real_chrome_th.ps1") | Out-Null
        Start-Sleep -Seconds 10
        if (Test-Url "$CdpUrl/json/version") { Write-Log "Chrome is up" } else { Write-Log "Chrome still not answering" }
    }

    if (-not (Test-Url "http://127.0.0.1:$AgentPort/health")) {
        Write-Log "price agent not answering on port $AgentPort, starting it"
        Start-Process -FilePath $python -WorkingDirectory $repo -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $logDir "price_agent.out.log") `
            -RedirectStandardError (Join-Path $logDir "price_agent.err.log") `
            -ArgumentList @("-m", "uvicorn", "scripts.price_agent:app", "--host", "127.0.0.1", "--port", "$AgentPort")
        Start-Sleep -Seconds 10
        if (Test-Url "http://127.0.0.1:$AgentPort/health") { Write-Log "price agent is up" } else { Write-Log "price agent still not answering, see logs/price_agent.err.log" }
    }

    Start-Sleep -Seconds $IntervalSeconds
}
