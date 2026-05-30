# DESIGN.md — Cozy Dashboard

The design language for every page. AI agents must read this before touching UI.
Reference: `docs/REDESIGN.md` (decision log) and `.interface-design/spec.md` (full spec).

## Lane
**Dense operator / industrial mission-control console.** A well-organized workbench, not a
spaceship and not a SaaS app. Warm enough to not feel clinical, sharp enough to feel like a
real instrument. Commit to this lane on every page — never drift into generic blue SaaS.

## Color
Surfaces: warm graphite scale `#070809 → #1d222b`, alpha hairline borders (no heavy lines).
Single accent: **signal amber `#e5954a`** — carries action, identity, and focus. Do not add a
second brand accent.
Status = instrument LEDs:
- green = running/ok, amber = idle/warn, muted = ended/disabled, red = fail
- blue = user/group/info, violet = assistant/sub-agent

Dark mode is primary. A warm light mode (paper-white `#f5f3f0`, deeper amber `#c77d2a`) is
supported; the amber accent carries through both.

## Type
Two families, by intent:
- **Space Grotesk** — display: wordmark, page/section titles, card names.
- **JetBrains Mono** — all telemetry: IDs, timestamps, cron expressions, logs, metrics.

Never let a whole page collapse to mono-only — pair display + mono for hierarchy.
Type scale must have real contrast (≥1.25 ratio between steps). Avoid flat 1.3–1.6 stacks of
9/10/11/12/13px. Body text **≥12px** (ideally 13px); reserve 9–11px for true labels/mono only,
not paragraph text. Labels: 11px / 500 / uppercase / +0.02em tracking.

## Spacing
4px base. Micro 4 · small 8 · medium 12–16 · large 24 · xl 32.
**Every bordered / colored / outlined container needs ≥12px inner padding.** No text or child
flush against a border or background edge (headers, toolbars, stat strips, panels, footers).

## Radius
Sharp-ish, technical: 4px (buttons/inputs/badges) · 6px (cards/dropdowns) · 8px (modals/panels).

## Signature elements
- **Pulse strip** — thin ambient health bar (system load · cron pulse · session count) that
  recedes when green and tints amber/red when something needs attention.
- Amber wordmark over a mono data field.
- Amber command brackets + faint calibration-dot grid on empty/no-session states.
- Amber top-hairline on the active/transcript panel.
- Inset amber/green status spine on session/cron rows.
- Emphasize-by-de-emphasizing: live rows bright+bold, ended/disabled rows recede.

## Banned defaults (AI slop — reject these)
- Blue SaaS accent (`#2563eb`), purple→blue gradients.
- `Inter` / `system-ui` / Arial as the body face (we use Space Grotesk + JetBrains Mono).
- Purple "assistant" pills, rounded-square icon tile above every heading.
- Cards nested in cards; everything wrapped in a card.
- **Colored glow box-shadows on dark** (the default "cool" AI look). Use subtle, purposeful
  lighting or none.
- Gray text on colored backgrounds; pure black/gray (always tint toward graphite).
- Dead-void empty states; bounce/elastic easing.
- Sub-AA contrast. Body text must hit ≥4.5:1, large/UI text ≥3:1.

## Depth
Dark mode = borders only (low-opacity rgba hairlines; shadows vanish on dark anyway).
Light mode = very subtle shadows (`0 1px 3px rgba(0,0,0,0.04)`).

## Motion
Theme toggle 200ms ease-out. Pulse dot 3s ease-in-out. Hover 120ms. No bounce/elastic.
Respect `prefers-reduced-motion`.

## Per-page intent
- **index/sessions** — split pane; transcript reads like an ops log, not chat sludge. Strong
  contrast between running/selected/ended. Violet = Cozy, amber = You, tools = flat subordinate.
- **cron** — dense table, 60+ rows scannable in 5s; status spine; clear last/next-run; no glow.
- **system** — one-glance confidence; health hierarchy; red/amber only when real.
- **logs** — level colors, fast filter/search, readable wrapping, mono detail.
- **keys** — provider status + last-used obvious; never expose secret material; AA contrast.

## Workflow (mandatory before shipping UI)
1. Read PRODUCT.md + DESIGN.md + docs/REDESIGN.md.
2. Make a surgical change (CSS/markup only unless scoped otherwise).
3. `npx impeccable detect <page>` — fix real issues; waive only sub-3px optical items.
4. Screenshot desktop (1440×900) + mobile (390×844) via CDP.
5. Confirm: status readable <5s? too SaaS? errors obvious-not-screaming? density useful? mobile ok?
6. critique → harden → polish on the changed page only.
