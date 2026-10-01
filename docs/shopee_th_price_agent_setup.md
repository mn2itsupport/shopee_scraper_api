# Shopee TH price via a real Chrome — setup guide

## Why this exists

Shopee TH only exposes price / rating / sold count through its internal
`pdp/get_pc` call. Shopee's anti-bot layer rejects that call (error
`90309999`) from every automated transport we tried. A **real, human-logged-in
Chrome** is not rejected (verified 2026-09-29: item `24682458079` returned
price 4–5 THB, original 8 THB).

So the API (on Railway) does not scrape price itself. It asks a small
**price agent** running next to a real Chrome for the live `get_pc` response.

```
Railway API ──HTTPS + token──▶ tunnel ──▶ price agent ──CDP──▶ real Chrome ──▶ shopee.co.th
 (shopee_th.py)                          (scripts/price_agent.py)  (logged in)
```

Title, images and description still come from the `curl_cffi` transport. If
the agent is down, the API still answers — with `price`, `rating`,
`sold_count` null.

Files involved:

| File | Role |
|---|---|
| `scripts/launch_real_chrome_th.ps1` | Starts Chrome with a debug port and its own profile |
| `scripts/price_agent.py` | HTTP endpoint `GET /price?url=…` (runs on the Chrome machine) |
| `app/scrapers/real_chrome.py` | Opens a tab in that Chrome and reads the `get_pc` response |
| `app/scrapers/sites/shopee_th.py` | Railway side: calls the agent and merges price fields |
| `app/config.py` | Settings (see "Settings reference") |

## Part 1 — The Chrome machine

Any always-on machine with a normal (residential) internet connection and
Google Chrome: your Windows PC, an office PC, a home server, a Thai VPS
(residential IP works best).

### 1.1 Install the project

```powershell
cd shopee_scraper_api
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 1.2 Configure `.env` on this machine

```
SHOPEE_TH_REAL_CHROME_CDP_URL=http://127.0.0.1:9222
SHOPEE_TH_PRICE_AGENT_TOKEN=<long random secret>
SHOPEE_TH_PRICE_PROBE_TIMEOUT_SECONDS=25
```

Generate the secret once and keep it — Railway needs the same value:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### 1.3 Start Chrome and log in (once per machine)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\launch_real_chrome_th.ps1
```

- Opens Chrome with `--remote-debugging-port=9222` and a dedicated profile at
  `browser_profiles/real_chrome_th` (Chrome refuses debugging on your default
  profile).
- **Log into Shopee TH by hand** in that window. The login is stored in that
  profile and survives restarts.
- Leave this Chrome running. The script assumes Chrome at
  `C:\Program Files\Google\Chrome\Application\chrome.exe` — edit the path if
  yours differs.

### 1.4 Start the agent

```powershell
.venv\Scripts\python.exe -m uvicorn scripts.price_agent:app --host 127.0.0.1 --port 8765
```

Local check (should return JSON containing `"item"`; without the header it
must return 401):

```powershell
curl -H "X-Agent-Token: <secret>" "http://127.0.0.1:8765/price?url=https://shopee.co.th/product-i.1253880486.24682458079"
```

Keep the agent bound to `127.0.0.1`; the tunnel (next step) is what exposes
it. **Never expose Chrome's port 9222** — it grants full control of the
browser and its logins.

### 1.5 Expose the agent with a tunnel

Railway must reach the agent over HTTPS. Pick one:

**Cloudflare Tunnel (recommended).** Install `cloudflared`.

- Quick test (URL changes on every restart):
  `cloudflared tunnel --url http://127.0.0.1:8765` — prints an
  `https://….trycloudflare.com` address.
- Stable hostname (needs a Cloudflare account + a domain): create a named
  tunnel in the Cloudflare Zero Trust dashboard, route a hostname such as
  `price-agent.yourdomain.com` to `http://127.0.0.1:8765`, and run it as a
  Windows service (`cloudflared service install <token>`).

**Tailscale Funnel** is an alternative if you already use Tailscale.

Test from outside your network:
`curl https://<hostname>/health` → `{"ok":true}`.

### 1.6 Keep it running after reboots (Windows)

Create two Task Scheduler tasks, trigger "At log on" (Chrome needs a logged-in
desktop session, so use auto-login on a dedicated PC):

1. `powershell -ExecutionPolicy Bypass -File <repo>\scripts\launch_real_chrome_th.ps1`
2. `<repo>\.venv\Scripts\python.exe -m uvicorn scripts.price_agent:app --host 127.0.0.1 --port 8765`
   (start-in directory: the repo's `shopee_scraper_api` folder)

Also run the tunnel as a service (1.5) so it starts by itself.

## Part 2 — Railway (the API)

In the Railway service's **Variables**:

```
SHOPEE_TH_PRICE_AGENT_URL=https://<your tunnel hostname>
SHOPEE_TH_PRICE_AGENT_TOKEN=<same secret as the agent>
SHOPEE_TH_PRICE_PROBE_ENABLED=true
SHOPEE_TH_PRICE_PROBE_MERGE=true
SHOPEE_TH_BROWSER_MODE_OVERRIDE=curl_cffi
```

Redeploy. `SHOPEE_TH_PRICE_PROBE_MERGE=true` is what puts the price into the
response (with `false` the probe only logs).

### Verify

```bash
curl -X POST https://<railway-app>/v1/shopee_th/pdp \
  -H "X-API-Key: sk_..." -H "Content-Type: application/json" \
  -d '{"url": "https://shopee.co.th/product-i.1253880486.24682458079"}'
```

Expect `price` set (4.0 for that item). In the Railway logs, look for
`shopee_th price probe recovered N field(s)`. `price agent failed` means the
agent/tunnel is unreachable or rejecting the token.

## Changing the Chrome machine later

Yes — nothing on Railway is tied to a specific machine. The only coupling is
the **agent URL** and the **token**. To move:

1. On the **new** machine, do Part 1 in full (install, `.env`, launch Chrome,
   **log into Shopee TH again** — logins live in that machine's Chrome
   profile and don't transfer, start the agent, start a tunnel).
2. Point Railway at the new machine:
   - **Named tunnel with a fixed hostname:** move the tunnel to the new
     machine (or create it there and re-route the same hostname). Railway
     needs **no change at all**.
   - **New hostname / quick tunnel:** update `SHOPEE_TH_PRICE_AGENT_URL` in
     Railway and redeploy.
   - Reusing the same token means no token change; use a new token (on both
     sides) if the old machine is retired or compromised.
3. Test with the `curl` verify step above.
4. Shut down the old agent/tunnel and log Chrome out on the old machine.

Tips: switch during a quiet period (price is null while neither machine
answers); to switch with zero downtime, bring the new machine up first and
only then update the Railway URL.

## Operations and troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `price` null, log `price agent failed` | Agent/tunnel down, wrong URL, or token mismatch (401) | Check `/health` through the tunnel; compare tokens on both sides |
| Agent returns 502 `chrome capture failed` | Chrome not running or not on port 9222 | Rerun `launch_real_chrome_th.ps1` |
| Log `get_pc error 90309999` | Shopee logged the Chrome profile out or flagged it | Open that Chrome, log in again, solve any captcha by hand |
| Slow responses | Agent handles one tab at a time on purpose | Expected; keep request volume modest |
| Price fields null for one product | Product unavailable or region-restricted | Check the product page in that Chrome |

Notes:

- The agent only accepts `shopee.co.th` URLs and requires the token, but
  anyone with the token can make your Chrome open Shopee pages — treat the
  token like a password and rotate it if leaked.
- Prices come back in Shopee's integer scale (× 100000); the API divides for
  you (`400000` → `4.0`). Multi-variant items report a range in
  `raw.data.item.price_min` / `price_max`.
- Use of a logged-in Shopee account for automated reads may conflict with
  Shopee's terms; it is the operator's responsibility.

## Settings reference

| Variable | Where | Meaning |
|---|---|---|
| `SHOPEE_TH_PRICE_AGENT_URL` | Railway | Base URL of the agent (via tunnel). Takes precedence over the CDP setting |
| `SHOPEE_TH_PRICE_AGENT_TOKEN` | Both | Shared secret (`X-Agent-Token`) |
| `SHOPEE_TH_REAL_CHROME_CDP_URL` | Chrome machine | Chrome's debug endpoint, `http://127.0.0.1:9222`. Also usable directly (no agent) when the API runs on the same machine as Chrome |
| `SHOPEE_TH_PRICE_PROBE_ENABLED` | Railway | Turns the price probe on |
| `SHOPEE_TH_PRICE_PROBE_MERGE` | Railway | `true` = merge probe fields into the response |
| `SHOPEE_TH_PRICE_PROBE_TIMEOUT_SECONDS` | Both | Per-request timeout (default 25) |
