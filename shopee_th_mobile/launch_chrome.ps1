# Starts the phone Chrome for shopee_th_mobile: its own profile
# (browser_profiles\real_chrome_th_mobile) and CDP port 9223, separate from
# the desktop agent's Chrome on 9222. Always presents as a phone (mobile UA,
# phone-sized window) so the account never flips between desktop and mobile.
#
# First run: log into Shopee TH by hand in the window it opens, using a
# DIFFERENT Shopee account from the desktop agent's. Then leave it open.
#
#   powershell -ExecutionPolicy Bypass -File shopee_th_mobile\launch_chrome.ps1

$chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$profile = Join-Path (Split-Path $PSScriptRoot -Parent) "browser_profiles\real_chrome_th_mobile"
New-Item -ItemType Directory -Force $profile | Out-Null

# Keep in sync with MOBILE_UA in capture.py.
$ua = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Mobile Safari/537.36"

Start-Process $chrome -ArgumentList @(
    "--remote-debugging-port=9223",
    "--remote-debugging-address=127.0.0.1",
    "--user-data-dir=`"$profile`"",
    "--user-agent=`"$ua`"",
    "--window-size=430,932",
    "https://shopee.co.th"
)
Write-Host "Phone Chrome started on CDP port 9223. Log into Shopee TH there if you haven't, then leave it open."
