# PRODUCT.md — Cozy Dashboard

## What this is
A private, single-operator control room for an OCPlatform AI agent system. It runs on a
VPS (Hetzner Helsinki), is reached over Tailscale, and is password-protected. It is **not**
a SaaS product, **not** multi-tenant, and has exactly one user.

## The one user
Sanket — 22, CEO of a ~30-person AI company (Nextbase), based in Surat. He opens this from a
MacBook or phone, usually mid-work, between Telegram messages, Notion tabs and code reviews.
He does not linger. This is a **glance-and-go** tool.

## Jobs to be done (in frequency order)
1. **Cron health** — "Is anything red? What fired recently? What runs next?" (most frequent)
2. **Sessions** — browse active/recent agent sessions, read transcripts.
3. **System health** — CPU / RAM / disk / services at a glance.
4. **Keys** — add / test / reorder API keys, spot a dead key.
5. **Logs** — tail and filter when debugging something that broke.

## What he needs to feel
Fast. In control. Not overwhelmed. With 60+ crons and 250+ sessions, **information density
matters** — but it must never feel like a raw spreadsheet. One glance should answer
"everything's fine" or "look at this."

## Success criteria
- Cron status is readable in <5 seconds.
- A failure (red) is unmissable but never screaming.
- Dense, but scannable — clear hierarchy, not a wall of equal-weight rows.
- Works on phone (Sanket checks from mobile).
- Reads like an **operator instrument**, not a chat app or a generic admin template.

## Constraints (hard)
- Static HTML/CSS/JS served by `serve.py`. **No build step.** Tailwind via Play CDN with
  `preflight:false` (see DESIGN.md). Do not introduce a bundler.
- Runtime JS builds class names via template literals (`status-${s}`, `badge-${t}`,
  `role-${r}`). Never purge/rename these dynamic classes.
- Do not touch backend (`serve.py`), endpoints, JS IDs/handlers, or OCPlatform core during
  pure UI work. UI changes are CSS/markup only unless explicitly scoped otherwise.
- Sub-agents / automations must never restart the gateway.
