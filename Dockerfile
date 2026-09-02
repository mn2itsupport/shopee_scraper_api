# Playwright's official image ships the exact Chromium build + OS-level
# dependencies (fonts, codecs, etc.) that playwright==1.47.0 expects — avoids
# hand-rolling apt-get for headless Chromium, which is the painful part of
# containerizing this app.
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app app
COPY scripts scripts

# Secrets (SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, ADMIN_DASHBOARD_PASSWORD,
# proxy credentials, ...) are NOT baked in here — set them as environment
# variables on the hosting platform instead. See .env.example for the full list.
ENV PLAYWRIGHT_HEADLESS=true

EXPOSE 8000

# Shell form so $PORT (injected by most platforms) is honored if set,
# falling back to 8000 for platforms that expect a fixed container port.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
