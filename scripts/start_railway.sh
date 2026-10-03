#!/usr/bin/env bash
# Container entrypoint. With SELF_HOSTED_CHROME=true it first starts a real
# Google Chrome (not Playwright's Chromium, and not launched by Playwright, so
# no automation flags) on a virtual display, with its profile on a Railway
# Volume, for shopee_th's price probe to attach to over CDP
# (SHOPEE_TH_REAL_CHROME_CDP_URL=http://127.0.0.1:9222) — the Linux/Railway
# equivalent of scripts/launch_real_chrome_th.ps1. Then it runs the API.
#
# Env (only read when SELF_HOSTED_CHROME=true):
#   CHROME_PROFILE_DIR      default /data/real_chrome_th — mount a Volume at /data
#   CHROME_UPSTREAM_PROXY   optional, e.g. http://user-session-abc123:pass@gw.example.com:7000
#                           — a STICKY Thai residential session; Chrome can't take
#                           proxy credentials on the command line, so gost forwards
#                           127.0.0.1:3128 to it and adds the auth.
#   VNC_PASSWORD            optional; when set, x11vnc serves the Chrome screen on
#                           port 5900 for the one-time manual Shopee login. Unset it
#                           again once logged in.
set -euo pipefail

if [ "${SELF_HOSTED_CHROME:-false}" = "true" ]; then
    export DISPLAY=:99
    profile_dir="${CHROME_PROFILE_DIR:-/data/real_chrome_th}"
    mkdir -p "$profile_dir"
    # Railway Volumes mount root-owned; Chrome runs as pwuser so it can keep
    # its sandbox (--no-sandbox is itself a detectable signal).
    chown -R pwuser:pwuser "$profile_dir"
    # A crash or redeploy can leave Chrome's single-instance lock behind,
    # which makes the next launch exit immediately.
    rm -f "$profile_dir"/Singleton*

    Xvfb :99 -screen 0 1366x768x24 -nolisten tcp &

    proxy_args=()
    if [ -n "${CHROME_UPSTREAM_PROXY:-}" ]; then
        gost -L "http://127.0.0.1:3128" -F "$CHROME_UPSTREAM_PROXY" &
        proxy_args=(--proxy-server=http://127.0.0.1:3128)
    fi

    if [ -n "${VNC_PASSWORD:-}" ]; then
        x11vnc -storepasswd "$VNC_PASSWORD" /tmp/vncpass >/dev/null
        x11vnc -display :99 -rfbport 5900 -rfbauth /tmp/vncpass -forever -shared -quiet &
    fi

    # Restart Chrome if it ever exits, so one crash doesn't take the price
    # probe down until the next redeploy.
    (
        while true; do
            runuser -u pwuser -- env DISPLAY=:99 TZ=Asia/Bangkok LANG=th_TH.UTF-8 \
                google-chrome \
                --user-data-dir="$profile_dir" \
                --remote-debugging-port=9222 \
                --remote-debugging-address=127.0.0.1 \
                --no-first-run \
                --no-default-browser-check \
                --disable-dev-shm-usage \
                --window-size=1366,768 \
                --lang=th-TH \
                "${proxy_args[@]}" \
                https://shopee.co.th || true
            echo "self-hosted Chrome exited; restarting in 5s" >&2
            sleep 5
            rm -f "$profile_dir"/Singleton*
        done
    ) &
fi

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
