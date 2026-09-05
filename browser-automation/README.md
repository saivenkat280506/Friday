# FRIDAY Browser Automation (Puppeteer, macOS)

Advanced browser control for FRIDAY: click, type, scroll, login, YouTube/Spotify, WhatsApp Web fallback.

Uses your **real Google Chrome profiles** (`~/Library/Application Support/Google/Chrome`, last-used profile by default). Chrome is quit and relaunched so cookies and logins are available. Set `CHROME_USE_REAL_PROFILE=0` to use the isolated `chrome-profile-data/` folder instead.

## Architecture

```
Python (backend/executor/puppeteer_client.py)
        │  HTTP JSON  POST /command
        ▼
Node control plane  (src/server.mjs  :3920)
        │
        ▼
Puppeteer + chrome-profile-data/  (dedicated FRIDAY profile)
```

Daily Chrome stays open. Automation uses `browser-automation/chrome-profile-data/` unless you set `CHROME_USE_REAL_PROFILE=1`.

## Start manually (optional)

```bash
cd browser-automation
PUPPETEER_SKIP_DOWNLOAD=true npm install
npm start
```

FRIDAY auto-starts the server on first browser tool use.

Clone cookies/logins from your daily Chrome profile (optional, Chrome should be closed):

```bash
npm run sync-profile
```

## Commands

`POST http://127.0.0.1:3920/command`

```json
{ "action": "youtube_play", "query": "AC/DC Back in Black" }
{ "action": "youtube_music_play", "query": "AC/DC Back in Black" }
{ "action": "spotify_login", "email": "...", "password": "..." }
{ "action": "spotify_search", "query": "AC/DC", "play": true }
{ "action": "scroll_test", "url": "https://en.wikipedia.org/wiki/AC/DC", "times": 8 }
{ "action": "navigate", "url": "https://example.com" }
{ "action": "whatsapp_send", "phone": "9198XXXXXXXX", "message": "hello" }
```

## Env

| Variable | Purpose |
|----------|---------|
| `CHROME_PATH` | Google Chrome binary (auto-detected on macOS) |
| `SPOTIFY_EMAIL` / `SPOTIFY_PASSWORD` | Optional automated Spotify login |
| `PUPPETEER_PORT` | Default `3920` |
| `PUPPETEER_HEADLESS` | Set `1` for headless |
| `CHROME_USE_REAL_PROFILE` | `1` to use your daily Chrome profile (close Chrome first) |

## FRIDAY voice / chat intents

- “Play Back in Black on YouTube”
- “Play Back in Black on YouTube Music”
- “Log in to Spotify”
- “Play Back in Black on Spotify”
- “Scroll speed test”
- “Run the demo” — scroll Wikipedia, then play on YouTube Music

### CLI one-shot

```bash
cd browser-automation
chmod +x run_linkedin_demo.sh
./run_linkedin_demo.sh
```
