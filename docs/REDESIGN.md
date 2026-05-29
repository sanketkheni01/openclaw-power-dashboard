# Cozy Dashboard — UI Redesign (Ultimate UI Design)

Date: 2026-05-29 · Model: anthropic/claude-opus-4-8 (Opus 4.8, configured & active)

## Aesthetic Direction
**Lane: Dense operator / industrial mission-control console.**

The root problem was incoherence: the sibling pages (`system.html`, `keys.html`,
`logs.html`, `cron.html`, `session.html`) already shared a distinctive industrial
terminal lane (graphite hull, **signal amber** accent, JetBrains Mono, LED status),
while the main `index.html` had drifted into generic blue SaaS — `#2563eb` accent,
`system-ui` body, purple assistant pills. That is exactly the "AI dashboard sludge"
the skill bans, and it fractured the product.

**Decision:** commit `index.html` fully to the existing operator lane and elevate the
whole product, rather than invent a third style.

- **Surfaces:** warm graphite scale `#070809 → #1d222b`, alpha hairline borders.
- **Accent:** signal amber `#e5954a` (single accent; carries action/identity/focus).
- **Status:** instrument LEDs — green running / amber idle / muted ended / red fail;
  blue = user/group/info, violet = assistant/sub-agent.
- **Type:** **Space Grotesk** (display: wordmark, titles) + **JetBrains Mono**
  (all telemetry, IDs, timestamps, logs). Two families + intent.
- **Signature elements:** amber wordmark over a mono data field · amber **command
  brackets** + faint **calibration-dot grid** on the no-session command panel ·
  amber top-hairline on the active transcript panel · inset amber/green **status
  spine** on session rows · emphasize-by-de-emphasizing (live rows bold+bright,
  ended rows recede).

## Defaults killed
Blue SaaS accent · purple assistant pills · `system-ui` body · header/toast
collision · default OS white scrollbars · dead-void empty state.

## Tailwind CSS Integration (req: Sanket)
**Approach chosen: Tailwind Play CDN (no build) + token-mapped theme + preflight OFF.**

Why not a PostCSS/CLI build: this is a **static, no-tooling project** (no
`package.json`/bundler) served by `serve.py`'s static handler, and **the JS builds
class names at runtime** via template literals — `status-${status}`, `badge-${type}`,
`role-${role}` (528 status dots, 445 badges, 100 role labels render live). A
JIT/purge build would silently drop those dynamic classes unless every variant were
safelisted — high regression risk for zero user benefit. The page already loads CDN
deps (marked.js, Google Fonts), so Play CDN matches the existing architecture and its
MutationObserver covers runtime-injected utilities.

What I did:
- Added `https://cdn.tailwindcss.com/3.4.16` + inline `tailwind.config`.
- **`corePlugins.preflight:false`** — Tailwind's reset is disabled so it cannot clobber
  the shipped operator design (verified: visual parity, body font intact).
- **Mapped my design tokens into the Tailwind theme** (single source of truth): colors
  `graphite/panel/shelf/ink/ink-2..4/amber/led.*`, `font-display`/`font-mono`,
  `border-hair*`, radii — so utilities like `bg-graphite text-amber font-display
  border-hair rounded-lg` resolve to the same CSS vars.
- **Converted static, inline-styled markup to utilities**: the empty-state command
  panel (signature element — brackets/grid/legend), the header dropdown menu. These
  have no JS dependency on their classes → safe.
- **Kept component CSS classes** that runtime JS generates (`.session-card`,
  `.badge-*`, `.role-*`, `.status-dot`, `.log-*`, `.toggle-switch.on`) in `<style>`.
  This is the deliberate safe boundary between Tailwind (static layout) and CSS
  (dynamic components).

Build/setup steps for handoff:
- **Current (shipped): none.** Just open the page — CDN loads Tailwind, config is inline.
- **If a production build is later desired** (offline/no-CDN, smaller payload):
  ```
  cd cozy-dashboard
  npm init -y && npm i -D tailwindcss@3.4.16
  # tailwind.config.js: content:['./index.html'], corePlugins:{preflight:false},
  #   theme.extend = (the same token map currently inline),
  #   safelist: [{pattern:/^(status|badge|role)-/}]  <-- REQUIRED for dynamic classes
  # input.css: @tailwind utilities;   (skip base to keep preflight off)
  npx tailwindcss -i input.css -o tw.css --minify
  # then swap the CDN <script> for <link rel=stylesheet href=tw.css>
  ```
  The `safelist` is mandatory or runtime `badge-${type}` etc. will be purged.

## Changed files (surgical)
- `index.html` — **only** file changed for the redesign. Pure CSS/markup:
  - `:root` token values remapped (names kept → ~140 var refs reskinned for free).
  - Literal colors remapped: blue→amber (accent) / blue→LED-blue (user/group/info),
    green→LED-green, purple→violet.
  - Added Space Grotesk import; display face on wordmark/titles/card names.
  - Status spine, amber panel hairline, refined role labels (mono), toast relocation
    (below header, elevated, left-accent), enriched empty state (command panel +
    brackets + calibration grid + status legend), dark instrument scrollbars,
    `scrollbar-gutter:stable`, composer/jump-button spacing, mobile header fixes.
- `index.html.predesign.bak` — pre-redesign snapshot for easy diff/rollback.
- `docs/redesign-shots/` — audit screenshots (incl. `06-tailwind-empty.png`,
  `07-tailwind-transcript.png` proving post-Tailwind parity + no regression).

**Untouched:** `serve.py` (backend), all endpoints, all JS behavior/IDs/handlers,
OpenClaw core. Gateway NOT restarted. (`serve.py` shows pre-existing edits from the
parallel live-bridge subagent — not mine.)

## Screenshots
- `docs/redesign-shots/01-list-empty.png` — session rail + command-panel empty state
- `docs/redesign-shots/02-transcript.png` — transcript detail (roles, tools, meta)
- `docs/redesign-shots/03-mobile.png` — 390px responsive
- `docs/redesign-shots/04-crons-tab.png` — Crons rail
- `docs/redesign-shots/05-system.png` — sibling page (lane confirmation)

## Validation
- Tailwind: `window.tailwind` loaded, `preflight===false` confirmed at runtime;
  `w-[380px]`→380px, `rounded-lg`→6px (from `--radius-lg`), body font = Space Grotesk.
- Regression check: 445 badges / 100 role labels / 528 status dots render after
  Tailwind load (dynamic JS classes intact); P1/P5 re-audited PASS on Tailwind shots.
- `<style>` tags balanced (1 open @39 / 1 close @770; the extra grep hit is a comment).
- `python3 -m py_compile serve.py` — untouched, still compiles.
- `curl -s localhost:3847/` → HTTP 200, 121 KB; asserted JS hooks present:
  sessionsGrid, detailPanel, logContainer, sendInput, micBtn, jumpToLatest,
  toastContainer, sysStats. Single balanced `<style>` block.
- Live render verified at 1440×900 and 390×844 via CDP screenshots.

## Audit record (mandatory loop)
Rendered real screenshots; ran targeted passes; patched top issues; re-rendered.

**Final: P1 PASS · P2 PASS · P3 reviewed (only real items fixed: scrollbar gutter,
composer/jump spacing; remainder sub-3px optical, waived per P5) · P4 SHIP · P5 PASS.**

Key patches across loops: toast/header collision → relocated; transcript width
clipping risk → `max-width:880px` + stable gutter; dead empty void → command panel
+ signature; faint metadata → contrast bump; weak signature → brackets + grid;
white scrollbars → dark instrument scrollbars; flat hierarchy → de-emphasized ended
sessions, stronger running dot.

## Risks / notes
- `.session-card:has(...)` de-emphasis uses `:has()` (fine on the deployed Chrome
  147 / all modern browsers; legacy browsers simply skip the dimming — no breakage).
- Web fonts load from Google Fonts (network dependency, matching existing pattern);
  mono fallback is graceful.
- Coordinated conceptually with the live-bridge subagent: I confined myself to
  `index.html` UI only, so backend/WS work won't conflict.
- `index.html.backup` (old) and `index.html.predesign.bak` (mine) both retained.
