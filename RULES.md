# RULES.md — Contribution & Privacy Rules

This is a **private, single-operator** dashboard for an OCPlatform agent system. The GitHub
repo is public-facing code only. **Real operational data must never be committed.**

Read this before every change. AI agents working on this repo: this file is binding.

## 🔒 Rule #1 — Never push private information

Before **every** commit and push, verify none of the following are staged or tracked:

- **Screenshots / images** of the running dashboard — they contain real session names,
  sub-agent labels, transcripts, commands, file paths, and project names. (`*.png`, `*.jpg`,
  `*.jpeg`, `*.gif`)
- **`outputs/`** and **`docs/redesign-shots/`** — UI-review artifacts, captured screens, run logs.
- **Runtime/state files** — `oauth-creds.json`, `topic-names.json`, `pinned.json`, `.openclaw/`.
- **Secrets** — API keys, tokens (OpenAI `sk-`*, GitHub `gh`-prefixed PATs, etc.), passwords, webhook URLs with tokens.
- **PII / infra** — phone numbers, personal emails, Tailscale IPs, internal hostnames,
  private domains, employee names/salaries, business roadmap details.
- **Real transcripts or message content** — anything pulled from live sessions.

These are all blocked by `.gitignore`. **Do not** override with `git add -f`.

## ✅ Pre-push checklist

Run this before pushing. If anything unexpected appears, stop and remove it.

```bash
# 1. Nothing ignored is being force-added; review what's staged
git status

# 2. Hard fail if any image/output/state file is tracked
git ls-files | grep -iE '\.(png|jpe?g|gif)$|^outputs/|^docs/redesign-shots/|^\.openclaw/|oauth-creds|topic-names|pinned\.json' \
  && { echo "❌ PRIVATE FILE TRACKED — abort push"; } || echo "✅ clean"

# 3. Scan staged diff for obvious secrets
git diff --cached | grep -nEi 'sk-[a-z]+-[A-Za-z0-9]|gh[ps]_[A-Za-z0-9]|BEGIN .*PRIVATE KEY|password[[:space:]]*[:=]' \
  && { echo "❌ POSSIBLE SECRET — abort push"; } || echo "✅ no obvious secrets"
```

## 📸 Need a screenshot for review?

Capture it into `outputs/` (git-ignored) and view/share it **outside** git (e.g. send via chat).
Never commit it. For docs that genuinely need a demo image, use a **synthetic/mocked** screenshot
with fake data only.

## 🧹 If private data was already pushed

1. Remove from working tree + add to `.gitignore`.
2. Purge from **all history**: `git filter-repo --invert-paths --path <path> --path-glob '*.png' ...`
3. Force-push: `git push origin master --force`.
4. ⚠️ On a **public** repo, old commit SHAs stay cached on GitHub after a force-push. To fully
   purge, **make the repo private** or **delete + recreate** it. Rotate any leaked credential.

(This exact incident happened on 2026-05-30 — screenshots with real session data were purged
from history. That's why these rules exist.)

## 🔑 Credentials

- Never embed tokens in the git remote URL or in committed files.
- Keep secrets in environment variables or untracked local files.
