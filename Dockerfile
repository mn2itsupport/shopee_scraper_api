# Playwright's official image ships the exact Chromium build + OS-level
# dependencies (fonts, codecs, etc.) that playwright==1.49.1 expects — avoids
# hand-rolling apt-get for headless Chromium, which is the painful part of
# containerizing this app. Keep this tag in lockstep with requirements.txt's
# playwright version — a mismatch fails browser_pool.py's chromium.launch()
# at startup with "Executable doesn't exist".
FROM mcr.microsoft.com/playwright/python:v1.49.1-jammy

WORKDIR /app

# Self-hosted real Chrome for shopee_th's price probe (only started when
# SELF_HOSTED_CHROME=true — see scripts/start_railway.sh): Google Chrome
# Stable rather than the bundled Chromium, Xvfb so it can run headed with no
# real display, x11vnc for the one-time manual login, Thai fonts so pages
# render like a real Thai user's, and gost to put proxy credentials in front
# of Chrome (which can't take them on its command line).
RUN apt-get update \
    && apt-get install -y --no-install-recommends wget gnupg xvfb x11vnc fonts-thai-tlwg \
    && wget -qO- https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
        > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends google-chrome-stable \
    && wget -qO- https://github.com/ginuerzh/gost/releases/download/v2.11.5/gost-linux-amd64-2.11.5.gz \
        | gunzip > /usr/local/bin/gost \
    && chmod +x /usr/local/bin/gost \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app app
COPY scripts scripts

# Secrets (SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, ADMIN_DASHBOARD_PASSWORD,
# proxy credentials, ...) are NOT baked in here — set them as environment
# variables on the hosting platform instead. See .env.example for the full list.
ENV PLAYWRIGHT_HEADLESS=true

EXPOSE 8000

# Strip CRLF in case the script was checked out on Windows — a "\r" after
# the shebang makes the container fail with "no such file or directory".
RUN sed -i 's/\r$//' scripts/start_railway.sh && chmod +x scripts/start_railway.sh

# Honors $PORT (injected by most platforms), falling back to 8000; starts the
# self-hosted Chrome first when SELF_HOSTED_CHROME=true.
CMD ["scripts/start_railway.sh"]
