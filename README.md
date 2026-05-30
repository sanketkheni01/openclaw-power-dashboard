# OpenClaw Sessions Dashboard

A real-time web dashboard for monitoring all your OpenClaw sessions — active agents, cron jobs, group chats, and direct conversations.

> ⚠️ **Before contributing or pushing, read [RULES.md](RULES.md).** This is a private,
> single-operator tool — **never commit screenshots, real session data, secrets, or PII.**
> All such files are git-ignored; do not override.

## Features

- **Live session monitoring** — auto-refreshes every 5 seconds
- **Activity feed** — see tool calls (🔧), responses (💬), thinking (🧠) in real-time
- **Session detail panel** — click any session to see the full transcript with color-coded entries
- **Smart filters** — All / Active / Recent / Groups / Crons / Direct / Busy
- **Stats bar** — total sessions, active count, cron jobs, groups at a glance
- **Cost tracking** — per-entry cost displayed in transcripts
- **Dark theme** — easy on the eyes

## Quick Start

### 1. Clone

```bash
git clone https://github.com/sanketkheni01/openclaw-power-dashboard.git
cd openclaw-power-dashboard
```

### 2. Configure paths

Edit `serve.py` and update these paths to match your OpenClaw installation:

```python
SESSIONS_FILE = '/root/.openclaw/agents/main/sessions/sessions.json'
TRANSCRIPTS_DIR = '/root/.openclaw/agents/main/sessions/'
```

Common locations:
- **Linux:** `/root/.openclaw/agents/main/sessions/`
- **macOS:** `~/.openclaw/agents/main/sessions/`

### 3. Run

```bash
python3 serve.py
```

Open `http://localhost:3847` in your browser.

### 4. (Optional) Run as a systemd service

Create `/etc/systemd/system/openclaw-dashboard.service`:

```ini
[Unit]
Description=OpenClaw Sessions Dashboard
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/openclaw-power-dashboard
ExecStart=/usr/bin/python3 serve.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now openclaw-dashboard
```

## How It Works

- `serve.py` — Python HTTP server that reads OpenClaw's `sessions.json` and JSONL transcript files
- `index.html` — Single-file frontend (no build step, no dependencies)
- Polls `/data/sessions.json` every 5 seconds for session list + recent activity
- Fetches `/data/transcript/<session_id>` on click for full transcript view

## API Endpoints

| Endpoint | Description |
|---|---|
| `GET /` | Dashboard UI |
| `GET /data/sessions.json` | All sessions with recent activity for active ones |
| `GET /data/transcript/<session_id>` | Full parsed transcript for a session |

## Requirements

- Python 3.6+
- A running OpenClaw instance (reads its session files directly)

## Customization

- **Port:** Change `PORT = 3847` in `serve.py`
- **Activity window:** Sessions active within 2 hours get activity data (change `7200000` ms in `serve.py`)
- **Refresh rate:** Change `setInterval(loadSessions, 5000)` in `index.html`

## License

MIT
