# Cozy Dashboard — Redesign Spec

## What This Is

A personal ops dashboard for **one person** (Sanket) managing an AI agent system (OpenClaw). It runs on a VPS, accessed via Tailscale, password-protected. Not a SaaS product. Not multi-tenant. It's a private control room for ~60 cron jobs, ~250 sessions, system health, API keys, and logs.

---

## Who Is This Human?

Sanket. 22, CEO, runs a 30-person AI company from Surat. Opens this dashboard from his MacBook or phone — usually mid-work, checking if crons are healthy, glancing at active sessions, or debugging something that broke. He's between Telegram messages, Notion tabs, and code reviews. He doesn't linger — it's a glance-and-go tool.

**What he does here:**
1. Check if cron jobs are healthy (most frequent — is anything red?)
2. Browse active sessions, read transcripts
3. Monitor system health (CPU/RAM/disk)
4. Manage API keys (add/test/reorder)
5. Tail logs when debugging

**What he needs to feel:**
Fast. In control. Not overwhelmed. He has 60+ cron jobs — information density matters, but it shouldn't feel like a spreadsheet.

---

## Domain Exploration

**Territory concepts:**
1. **Control room** — not a spaceship, more like a home studio mixing board. Intimate, personal, everything within arm's reach
2. **Pulse** — the system has a heartbeat. Sessions are alive, crons fire rhythmically, health metrics breathe
3. **Observatory** — watching autonomous agents work. You're the observer, not the operator of every action
4. **Workshop bench** — tools laid out, organized, functional. Not pristine — used
5. **Night watch** — most usage is checking that everything's running while you do other things

**Color world (what exists in this domain):**
1. Terminal green on dark — the universal "system alive" signal
2. Warm amber — the desk lamp in late-night monitoring. Alert without alarm
3. Slate blue — server racks, cool metal, structured
4. Soft white on dark graphite — readable, low eye strain for quick checks
5. Muted red — something needs attention, not screaming
6. Ink black — deep backgrounds that recede, letting content float

**Signature element:** **The pulse indicator** — a unified health heartbeat visible on every page. Not just a green dot, but a contextual status line that shows the system's rhythm: last cron fire, active sessions count, and system load as a single ambient strip. It tells you "everything's fine" or "look at this" in one glance without needing to navigate.

**Defaults to reject:**
1. ❌ Ultra-dark (#050507) backgrounds → ✅ Graphite dark (#111116) with proper elevation — current is so dark it has no room for layering
2. ❌ 3-column masonry card grid for crons → ✅ Dense table/list view with inline status — cards waste space for 60+ items
3. ❌ Sidebar-only navigation → ✅ Top nav with page tabs + collapsible sidebar for session list where it makes sense

---

## Design Direction

### Theme: "Workshop Console"

A dense, functional control interface that feels like a well-organized workbench — everything has its place, nothing decorative. Warm enough to not feel clinical (amber accents on graphite), sharp enough to feel like a real tool.

### Dual Theme System

**Dark mode** (default): Graphite surfaces with warm amber accent. This is the primary experience — most usage is quick checks, often at night.

**Light mode**: Warm paper-white surfaces with slate-ink text. Not a bleached-white corporate look — more like a well-lit workshop desk. Same amber accent carries through.

Theme toggle in the header. Respects `prefers-color-scheme` on first load, then remembers user preference in localStorage.

### Token Architecture

```
DARK MODE:
--canvas:        #111116     (page background)
--surface-1:     #18181e     (cards, sidebar)
--surface-2:     #1f1f26     (elevated: dropdowns, modals)
--surface-3:     #26262e     (highest elevation)
--surface-inset: #0c0c10     (inputs, code blocks — darker than canvas)

--ink-1:         #e8e8ec     (primary text)
--ink-2:         #9898a4     (secondary)
--ink-3:         #5c5c6a     (tertiary/labels)
--ink-4:         #3a3a46     (muted/disabled)

--line-1:        rgba(255,255,255, 0.06)   (subtle separator)
--line-2:        rgba(255,255,255, 0.10)   (standard border)
--line-3:        rgba(255,255,255, 0.16)   (emphasis border)

--accent:        #e89b3e     (warm amber — primary action, active states)
--accent-dim:    rgba(232,155,62, 0.10)
--accent-text:   #f0b060     (amber on dark for readability)

--status-ok:     #22c55e
--status-warn:   #eab308
--status-error:  #ef4444
--status-info:   #3b82f6

LIGHT MODE:
--canvas:        #f5f3f0     (warm off-white)
--surface-1:     #ffffff     (cards)
--surface-2:     #ffffff     (elevated)
--surface-3:     #fafaf8     (highest)
--surface-inset: #eeece8     (inputs)

--ink-1:         #1a1a22     (primary text)
--ink-2:         #5c5c6a     (secondary)
--ink-3:         #8c8c98     (tertiary)
--ink-4:         #b4b4be     (muted)

--line-1:        rgba(0,0,0, 0.04)
--line-2:        rgba(0,0,0, 0.08)
--line-3:        rgba(0,0,0, 0.14)

--accent:        #c77d2a     (slightly deeper amber for contrast on white)
--accent-dim:    rgba(199,125,42, 0.08)
--accent-text:   #b06d1e

--status-ok:     #16a34a
--status-warn:   #ca8a04
--status-error:  #dc2626
--status-info:   #2563eb
```

### Typography

**Primary:** `Inter` — geometric, clean, excellent at small sizes. Dashboard is information-dense, Inter handles 12-13px beautifully.
**Mono:** `JetBrains Mono` — for IDs, timestamps, cron expressions, log lines.

Hierarchy:
- **Page title:** 15px / 600 weight / -0.01em tracking
- **Section header:** 13px / 600 / -0.005em
- **Body:** 13px / 400
- **Label:** 11px / 500 / 0.02em tracking / uppercase
- **Mono data:** 12px / 400 / JetBrains Mono

### Layout & Navigation

**Top bar:** OpenClaw branding + pulse strip (system health at-a-glance) + theme toggle + refresh
**Tab bar:** Sessions | Crons | System | Logs | Keys — horizontal tabs below the header

**Pages:**

1. **Sessions** — Left sidebar list (as-is, but refined) + right transcript panel. Keep the current split-pane approach — it works.

2. **Crons** — The big redesign. Current 3-column card grid doesn't scale to 60+ jobs. New layout:
   - **Table view** (default): Dense rows. Columns: Status dot | Name | Schedule | Last run | Duration | Next run | Model | Type badges | Actions (toggle, run now)
   - **Card view** (toggle): For when you want more detail. 2-column grid.
   - **Filters:** Status (all/active/disabled/errored), search, sort by name/last-run/schedule
   - **Bulk actions:** Enable/disable selected

3. **System** — Current layout is good but sparse. Add:
   - Health history sparklines for CPU/RAM/disk (last hour)
   - Service status with uptime
   - Top processes table (keep)
   - Active connections count

4. **Logs** — Tail view with auto-scroll, level filters (info/warn/error), search

5. **Keys** — Card-based, each key shows provider, status, last-used, usage stats

### Depth Strategy

**Borders-only** in dark mode. Subtle, low-opacity rgba borders. No drop shadows on dark — they disappear anyway.
**Subtle shadows** in light mode. Very light box-shadows on cards (0 1px 3px rgba(0,0,0,0.04)).

### Spacing

Base unit: **4px**
- Micro: 4px (icon gaps, inline spacing)
- Small: 8px (within components)
- Medium: 12-16px (card padding, between items)
- Large: 24px (section gaps)
- XL: 32px (page-level spacing)

### Border Radius

Sharp-ish. Technical feel.
- Small: 4px (buttons, inputs, badges)
- Medium: 6px (cards, dropdowns)
- Large: 8px (modals, panels)

### Signature: Pulse Strip

A thin (28px tall) ambient bar below the top header that shows:
- **Left:** System status — CPU ░░░ 12% | RAM ░░░ 38% | Disk ░░░ 37% (mini progress bars)
- **Center:** Cron pulse — "38 active · 23 disabled · last fired 6m ago" with a subtle heartbeat animation on the dot
- **Right:** Sessions — "3 active · 244 total"

This strip is visible on ALL pages. It's the system's heartbeat. When everything's green, it fades into the background. When something's wrong, the relevant segment gets an amber/red tint.

### Component Patterns

**Status badges:**
- OK: green dot + "OK" text
- Error: red dot + "error" text + slightly tinted red background
- Disabled: gray dot + muted text + dashed border
- Running: amber dot + pulse animation

**Action buttons:**
- Primary: Filled amber (--accent), white text
- Secondary: Ghost with border, text color on hover
- Danger: Ghost red, fills red on hover

**Tables:**
- Alternating row backgrounds (surface-1 / canvas)
- Hover: surface-2
- Sticky header
- Sortable columns with directional arrows

**Session list items:**
- Active: left amber accent border (2px)
- Inactive: no accent
- Hover: surface-2 background
- Selected: surface-2 + amber left border

### Responsive

Mobile-usable (Sanket checks from phone sometimes). The pulse strip stacks vertically on small screens. Session sidebar becomes full-screen with back button. Tables get horizontal scroll.

### Animation

- Theme toggle: 200ms ease-out on all color transitions
- Pulse dot: 3s ease-in-out infinite opacity
- Tab switch: no animation, instant
- Hover states: 120ms ease
- Loading states: subtle skeleton shimmer

---

## Pages Breakdown

### 1. Sessions Page (index.html)

```
┌─────────────────────────────────────────────────────┐
│ 🟢 OpenClaw Dashboard          [☀/🌙] [↻]         │
├─────────────────────────────────────────────────────┤
│ CPU ▓░ 12% │ RAM ▓▓░ 38% │ 38 crons ok │ 3 active │
├─────────────────────────────────────────────────────┤
│ Sessions │ Crons │ System │ Logs │ Keys            │
├──────────────┬──────────────────────────────────────┤
│ [🔍 search]  │                                      │
│ 244 sessions │  Session Transcript                  │
│ 3 active     │                                      │
│──────────────│  "Redesign Cozy Dashboard"           │
│ ● Session 1  │  claude-opus-4-6 · Todos             │
│ ● Session 2  │  Started 5m ago                      │
│ ○ Session 3  │                                      │
│ ○ Session 4  │  [transcript messages...]            │
│ ...          │                                      │
└──────────────┴──────────────────────────────────────┘
```

### 2. Crons Page (cron.html)

```
┌─────────────────────────────────────────────────────┐
│ [Pulse strip]                                       │
├─────────────────────────────────────────────────────┤
│ Sessions │ *Crons* │ System │ Logs │ Keys           │
├─────────────────────────────────────────────────────┤
│ 61 total · 38 active · 23 disabled · 6m ago        │
│ [🔍 search]  [all|active|disabled|errored] [≡ ⊞]  │
├─────────────────────────────────────────────────────┤
│ ● │ Task Sync (15min)     │ every 15m │ 6m ago │ … │
│ ● │ openclaw-radar         │ every 1h  │ 12m   │ … │
│ ● │ CEO Time Tracker       │ hourly    │ 25m   │ … │
│ ● │ X Feed Monitor         │ every 1h  │ 54m   │ … │
│ ○ │ disabled-job           │ cron expr │ -     │ … │
│ ...                                                 │
└─────────────────────────────────────────────────────┘
```

### 3. System Page (system.html)

```
┌─────────────────────────────────────────────────────┐
│ [Pulse strip]                                       │
├─────────────────────────────────────────────────────┤
│ Sessions │ Crons │ *System* │ Logs │ Keys           │
├──────────┬──────────┬──────────┬────────────────────┤
│ CPU      │ MEMORY   │ DISK     │ NETWORK            │
│ 11.5%    │ 11.4/30G │ 112/301G │ rx 5.2G tx 3.4G   │
│ ▁▂▃▂▁▃▂ │ ▅▅▅▅▅▅▆ │ ████░░░░ │ conns: 247         │
├──────────┴──────────┴──────────┴────────────────────┤
│ SERVICES                                            │
│ ● openclaw-gateway  active  2d 3h                   │
│ ● cozy-dashboard    active  2d 3h                   │
│ ● tailscale         active  uptime                  │
├─────────────────────────────────────────────────────┤
│ TOP PROCESSES (CPU)                                 │
│ PID │ USER │ CPU% │ MEM% │ RSS │ COMMAND            │
│ ... │      │      │      │     │                    │
└─────────────────────────────────────────────────────┘
```

---

## Technical Implementation

- **Stack:** Pure HTML/CSS/JS (no build step) — matches current approach. Each page is self-contained.
- **Theme:** CSS custom properties with `[data-theme="dark"]` / `[data-theme="light"]` on `<html>`. JS toggles the attribute and persists to localStorage.
- **Fonts:** Google Fonts — Inter (400, 500, 600) + JetBrains Mono (400, 500)
- **Icons:** Inline SVGs (no icon library dependency) — keep it minimal, ~10 icons total
- **API:** No changes to serve.py endpoints. All existing APIs stay the same.
- **WebSocket:** Keep existing WS connection for live session updates

---

## What NOT to Change

- serve.py backend — all APIs stay identical
- WebSocket protocol
- Authentication (password in serve.py)
- Data structures
- File naming (index.html, cron.html, etc.)

---

## Priority Order

1. Shared CSS foundation (tokens, theme toggle, pulse strip, nav) 
2. Sessions page (most used)
3. Crons page (biggest visual change — card grid → table)
4. System page (refinement)
5. Logs page
6. Keys page
