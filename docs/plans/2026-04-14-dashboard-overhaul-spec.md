# OpenClaw Dashboard — Complete Overhaul Spec

> **Vision:** A web-based Work OS for OpenClaw — not just monitoring, but a single platform to use all power features. Inspired by VS Code (fixed layout) and Codex (minimal session sidebar, chat-first UX).

> **UI Reference:** OpenAI Codex IDE (screenshot provided Apr 14)

---

## Part 1: What Is This?

The OpenClaw Dashboard is evolving from a monitoring tool into a **full OpenClaw web interface** — a Work OS where you control everything from one screen. Sessions, crons, logs, keys, config, system — all panels in one unified app. You never leave.

**Core actions:**
1. **Chat with sessions** — Send messages, read transcripts, interact with agents
2. **Monitor everything** — Sessions, crons, system health, logs — all visible
3. **Manage infrastructure** — API keys, cron jobs, gateway config, restart
4. **Search & navigate** — Cmd+K command palette across all entities

---

## Part 2: Current Codebase Audit

### File Inventory

| File | Lines | Size | Purpose |
|------|-------|------|---------|
| `index.html` | 2,504 | 114KB | Main dashboard — session list, transcript viewer, send bar, keys modal |
| `serve.py` | 2,325 | 99KB | Python backend — all API endpoints, WebSocket, SSE, system stats |
| `cron.html` | 978 | 30KB | Standalone cron job management page |
| `session.html` | 558 | 26KB | Standalone session transcript viewer |
| `keys.html` | 548 | 31KB | Standalone API keys management page |
| `logs.html` | 480 | 20KB | Real-time log viewer with SSE streaming |
| `system.html` | 196 | 8KB | System stats page |
| `server.js` | ~200 | 7KB | **Legacy — unused, delete** |

**Total active code: ~7,600 lines across 7 files → will merge into 1 SPA**

### Backend API Surface (serve.py — NOT being modified)

**Data Endpoints (GET):**
| Endpoint | Returns |
|----------|---------|
| `/data/sessions.json` | All sessions + metadata + activity + parent-child + stats + topic names. ETag. |
| `/data/system.json` | Full system info: CPU, Memory, Disks, Network, System, Services, Top 10 procs |
| `/data/cron-jobs.json` | All cron jobs |
| `/data/cron-runs/<jobId>` | Last 20 run entries for a cron job |
| `/data/transcript/<sessionId>` | Paginated transcript (role, content, model, timestamps, usage, tool calls) |
| `/data/logs` | Parsed NDJSON logs with date/level/subsystem/pagination |
| `/data/logs/stream` | SSE real-time log streaming |
| `/api/system-stats` | Lightweight CPU%/RAM%/Disk% |
| `/api/keys` | Auth profiles from config |
| `/api/keys/usage` | Per-key usage stats |
| `/api/keys/oauth/usage` | OAuth account usage from Anthropic API |

**Action Endpoints (POST):**
| Endpoint | Action |
|----------|--------|
| `/api/send-message` | Send text to session via gateway WebSocket |
| `/api/transcribe` | Audio → Groq Whisper → text |
| `/api/restart-gateway` | `openclaw gateway restart` |
| `/api/refresh-topics` | Force Telegram topic name refresh |
| `/api/pin` | Toggle session pin |
| `/api/keys/add\|delete\|toggle\|reorder\|test\|test-all` | Key lifecycle |
| `/api/keys/oauth/start\|complete\|update\|remove` | OAuth PKCE flow |
| `/api/cron/toggle\|run` | Cron job control |

**Real-Time:**
- **WebSocket (port 3848):** watchdog monitors sessions.json → broadcasts to all clients
- **SSE `/data/logs/stream`:** Real-time log entry streaming

**Background Threads:**
1. Topic name refresh (every 5 min) — transcript scanning + Telegram API
2. File watcher — watchdog on sessions.json → WS broadcast
3. WebSocket server — asyncio on port 3848

### Session Data Model
```
{
  key: "agent:main:telegram:group:-100383...:topic:17944",
  sessionId: "uuid",
  label: "Human-readable label",
  updatedAt: 1713120000000,
  sessionType: "main" | "subagent" | "cron" | "cron-run" | "telegram" | "other",
  status: "running" (<5min) | "idle" (<1h) | "completed",
  pinned: bool,
  activity: [{role, model, stop, ts, cost, action, detail}],
  children: [{key, sessionId, updatedAt, label}],
  parentKey, parentLabel
}
```

---

## Part 3: Current Features (What Exists Today)

### Main Dashboard (index.html)
- **Header:** Logo + system stats bars (CPU/RAM/Disk) + refresh + ⋮ menu
- **Sidebar (320px):** 3 tabs (Sessions/Agents/Crons), session cards with status dot, name, badges, model pill, token bar, last message preview, time ago. Virtual scroll.
- **Detail panel:** Panel header + meta bar + tabs (Transcript/Sub-agents) + Markdown transcript rendering (marked.js) + collapsible tool calls + thinking blocks + inline images + infinite scroll + send bar (text + voice + send)
- **Keys modal:** Full OAuth PKCE + key management
- **Hidden DOM elements:** Cost dashboard, toolbar, filter bar, breadcrumb, timeline, status bar
- **WebSocket:** Auto-reconnect, sound + toast on new activity
- **Keyboard:** j/k navigate, Enter open, Esc close, / search, ? help
- **Settings:** Sound alerts, toast notifications, auto-refresh toggles

### Cron Jobs (cron.html)
- Stats bar (total/enabled/failed/active), filter bar + search, sort options
- Job cards: name, status dot, schedule, last run + result, payload type
- Detail: full schedule, payload, last 20 runs with status/duration/errors
- Actions: toggle enable/disable, run now

### Logs (logs.html)
- Level filters (INFO/WARN/ERROR) with counts, subsystem dropdown, date picker
- Live SSE streaming with auto-scroll + pin
- Entries: timestamp, level badge, subsystem, message, expandable JSON
- Keyboard: l=live, p=pin, b=bottom

### API Keys (keys.html)
- Summary stats, OAuth login + accounts with usage bars/rate limits
- Key cards: name, provider, position, toggle, test, move, usage stats
- Add key form, auto-refresh 60s

### Session (session.html)
- Standalone transcript viewer for `/session/<id>` (open in new tab)
- Same rendering as main dashboard detail panel

### System (system.html)
- Cards: CPU, Memory, Disk(s), Network, System, Services, Top Processes table
- Auto-refresh 10s

---

## Part 4: Design Decisions (Locked)

### Vision
**Work OS for OpenClaw.** Single platform, everything in one screen. Inspired by VS Code (fixed panel layout) and Codex (minimal sidebar, chat-first).

### Reference: Codex UI Patterns (from screenshot)
- **Sidebar:** Navigation items at top (New chat, Search, Plugins, etc.) → Sessions grouped under collapsible project headers → Settings at bottom
- **Session items:** Ultra-minimal — **just name + time ago**. No badges, no tokens, no status dots, no model pills. One line per session.
- **"Show more"** for long lists within a group
- **Empty state:** App logo + tagline + project selector dropdown + suggested prompts + input bar
- **Input bar:** Full-width at bottom of main area. Model selector + voice + send. Prompt suggestions above.
- **Right panel (optional):** Tabs for different views (Summary, Review, Browser)
- **Status bar:** Bottom strip with connection info, mode indicators

### Layout (VS Code Fixed)
```
┌─────────────────────────────────────────────────────┐
│  Header: Brand + System Stats + ⌘K + Menu           │
├────────────┬────────────────────────────────────────┤
│            │                                        │
│  Sidebar   │         Main Panel                     │
│  (280px)   │    (Chat / Transcript / Empty State)   │
│            │                                        │
│  Nav items │                                        │
│  Sessions  │                                        │
│  (grouped) │                                        │
│            │                                        │
│            ├────────────────────────────────────────┤
│            │  Bottom Panel: Logs (collapsible, 200px)│
│            │  Toggle with Ctrl+` like VS Code       │
├────────────┴────────────────────────────────────────┤
│  Status Bar: WS status · Sessions count · Shortcuts  │
└─────────────────────────────────────────────────────┘
```

### Sidebar Design (Codex-inspired)

**Top navigation items:**
- 🔍 Search (opens Cmd+K palette)
- ⏰ Crons (switches main panel to cron manager view)
- 🔑 Keys (opens keys modal)
- ⚙️ Settings (opens settings modal)

**Session list (below nav):**
- **5 filter tabs:** All | Sessions | Agents | Crons | Pinned
- **Each session item:** Just `Session name` + `time ago` — ONE LINE
  - Running sessions: subtle green dot before name (only indicator)
  - Selected session: highlighted background
  - Pinned sessions: small pin icon
  - No badges, no tokens, no model, no type indicator on the card
- **Collapsible sections** (optional, within active tab):
  - When "All" tab is active, group by: 🟢 Running → 📌 Pinned → Recent → Older
- **"Show more"** link for sections with many items

**Bottom of sidebar:**
- System health micro-indicator (green/yellow/red dot + "System OK" or "CPU 85%")
- Connection status (WS connected/disconnected)

### Session Cards (Ultra-Minimal)
```
┌─────────────────────────────────────┐
│ ● Session name here              2m │
└─────────────────────────────────────┘
```
- Green dot only for running sessions (hidden for idle/completed)
- Name truncated with ellipsis
- Time ago right-aligned, muted
- Hover: subtle background change
- Selected: accent background tint
- That's it. All detail goes to the main panel.

### Main Panel — Chat/Transcript View
- **Panel header:** Session name (large, clear) + meta info (model, tokens, type badge, time) + action buttons (copy, fullscreen, new tab, close)
- **Transcript area:** Role-colored left borders, Markdown rendering, collapsible tool calls, thinking blocks, inline images, infinite scroll
- **Send bar at bottom:** Text input + voice record + send button + model display
- **Per-message hover actions:** Copy, Reply, Pin

### Main Panel — Empty State (Codex-inspired)
```
       [OpenClaw Logo]

      Welcome to OpenClaw

   Select a session from the sidebar
       or start a new chat

  ┌─────────────────────────────────┐
  │ Ask OpenClaw anything...     🎤 ▶│
  └─────────────────────────────────┘
```
- Shows when no session is selected
- Input bar functional — sends to main session by default
- Optional: suggested quick actions below ("View running agents", "Check cron health", "System status")

### Bottom Panel — Logs (Collapsible)
- Toggle with `Ctrl+`` (VS Code muscle memory)
- Real-time SSE log stream with level/subsystem filters
- Compact single-line entries: `HH:MM:SS LEVEL subsystem message`
- Color-coded by level (blue/yellow/red)
- Default: collapsed (hidden). Opens on demand.
- Height resizable (drag top edge)

### Status Bar (Bottom)
- Left: WebSocket indicator (green/red dot + "Connected"/"Reconnecting"), session count ("241 sessions · 3 active")
- Right: Keyboard shortcut hint ("? for shortcuts"), last updated timestamp

### Header
- Left: OpenClaw logo + "OpenClaw" text (no "Dashboard" — it's the whole app now)
- Center: System stats — compact: `CPU 12% · RAM 68% · Disk 45%` (color-coded text, no bars)
- Right: Cmd+K search button + ⋮ menu (Restart Gateway, Refresh Topics)

### Command Palette (Cmd+K)
- Full-screen overlay with search input
- Searches across: session names, cron job names, commands
- Results grouped: Sessions, Crons, Actions
- Actions: "Restart Gateway", "Refresh Topics", "Open Logs", "Open Keys"
- Arrow keys to navigate, Enter to select, Esc to close

### Cron Manager (Integrated View)
- When "Crons" clicked in sidebar nav, main panel switches to cron view
- Same layout as current cron.html but styled to match new theme
- Job list with inline actions (toggle, run now)
- Expandable run history per job
- Never leaves the SPA — sidebar stays visible

### Keys Manager
- Modal overlay (like current, but restyled)
- OAuth PKCE flow, key list, add/remove/test/reorder
- Stays as modal — not a panel

### Theme
- **Ultra-dark** — continue Precision & Density palette (#050507 root)
- **system-ui** font family (keep JetBrains Mono for code/data)
- **Borders-only depth** (no shadows)
- **Accent:** Blue #2563eb for selected/active states
- **Status colors:** Green #16a34a, Yellow #ca8a04, Red #dc2626
- **Sharp corners:** 4px radius max
- **Font size:** 13px base, 11px secondary, 10px tertiary

---

## Part 5: Features to Add (Web-Feasible)

### Must Have (v1)
- [ ] Unified SPA — merge all pages into index.html, client-side view switching
- [ ] Codex-style minimal session cards (name + time only)
- [ ] Sidebar filter tabs: All | Sessions | Agents | Crons | Pinned
- [ ] Collapsible bottom logs panel (Ctrl+`)
- [ ] Command palette (Cmd+K)
- [ ] Status bar (WS status, session count, shortcuts)
- [ ] Codex-style empty state with input bar
- [ ] Cron manager as inline view (not separate page)
- [ ] Resizable sidebar (drag handle)
- [ ] Per-message hover actions (copy, reply)
- [ ] Keyboard-first (expand shortcuts)

### Should Have (v1.1)
- [ ] Plan Mode (inline annotation on assistant messages)
- [ ] Pinned messages bar at top of transcript
- [ ] Better code syntax highlighting (highlight.js CDN)
- [ ] In-app notifications feed (agent completions, cron failures)
- [ ] Token display toggle in settings
- [ ] Config viewer (read-only JSON)
- [ ] Text selection → follow-up popover

### Nice to Have (Backlog)
- [ ] Sub-agent steer/kill from sidebar
- [ ] Session grouping by project (custom groups)
- [ ] Suggested quick actions in empty state
- [ ] Bookmark messages
- [ ] Cost tracking (per-session, daily totals)

---

## Part 6: Problems to Solve

### Architecture
1. 6 separate HTML files with ~700 lines duplicated CSS each → merge into 1 SPA
2. No client-side routing → add view switching (sessions/crons/logs/keys)
3. Legacy dead code (server.js, hidden DOM elements) → clean up
4. No shared design system → unified CSS variables + components

### Design
5. Theme fragmentation (5 pages on old amber theme) → one unified dark theme
6. Session cards too noisy → ultra-minimal (Codex-style)
7. No visual hierarchy → clear primary/secondary/tertiary zones
8. System stats underutilized → compact text in header
9. Lazy empty state → functional Codex-style empty state with input

### Functionality
10. Search/filter hidden → Cmd+K command palette
11. Cron management on separate page → integrated view
12. Log viewer on separate page → collapsible bottom panel
13. No session grouping → filter tabs + optional collapsible groups
14. Transcript lacks summary → meta bar with model/tokens/type/time
15. WebSocket status invisible → status bar indicator
16. No per-message actions → hover menu (copy, reply)
17. No command palette → Cmd+K
18. No keyboard for everything → expand shortcut set

---

## Part 7: Remaining Questions

**Q1: Transcript style?**
A) Chat bubbles (WhatsApp-like)
B) Log viewer (current role badges + content blocks)
C) IDE/terminal (monospace, dense)

**Q2: Send message — how often do you use it?**
A) Often — keep prominent (bottom of main panel)
B) Rarely — tuck into menu/shortcut
C) Never — remove

**Q3: Cost visibility?**
A) Show daily/weekly/monthly in header
B) Per-session only (in detail panel)
C) Keep hidden

**Q4: Anything else you want that's not here?**
