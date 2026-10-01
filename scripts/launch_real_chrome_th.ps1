# Starts a real Chrome with a CDP debug port for shopee_th's price probe
# (SHOPEE_TH_REAL_CHROME_CDP_URL=http://127.0.0.1:9222).
#
# Uses its own profile dir (Chrome refuses remote debugging on the default
# profile). First run: log into Shopee TH by hand in the window it opens;
# the login is then kept in that profile for later runs. Leave this Chrome
# running while the API is up.
#
#   powershell -ExecutionPolicy Bypass -File scripts\launch_real_chrome_th.ps1

$chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$profile = Join-Path (Split-Path $PSScriptRoot -Parent) "browser_profiles\real_chrome_th"
New-Item -ItemType Directory -Force $profile | Out-Null

Start-Process $chrome -ArgumentList @(
    "--remote-debugging-port=9222",
    "--remote-debugging-address=127.0.0.1",
    "--user-data-dir=`"$profile`"",
    "https://shopee.co.th"
)
Write-Host "Chrome started. Log into Shopee TH there if you haven't, then leave it open."
