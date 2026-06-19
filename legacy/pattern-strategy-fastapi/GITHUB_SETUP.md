# GitHub Setup — Pattern Strategy (SLC) Bot

How to put this repo on GitHub **safely**. The package ships with live credentials in the
original team-share; this copy has been **scrubbed** so it is safe to push. Read the
checklist before you run any `push`.

---

## 0. Status / what to know first

- **No GitHub connector is attached to Claude** (the connected ones are Slack, Vercel,
  Morningstar, Supabase). So the repo is created and pushed **from your machine** with the
  GitHub CLI (`gh`) or plain `git` — not by Claude. Even if you connect GitHub later, do
  **not** push the live `config.yaml` / `WEBHOOK_AND_SECRETS.md` through it.
- **Default to a PRIVATE repo.** Go public only after you've rotated every token and rely
  solely on `config.example.yaml`.

## 1. What was already done to this copy

- Deleted `trading-bot/config.yaml` (live Telegram/Discord/X creds).
- Deleted `WEBHOOK_AND_SECRETS.md` (same creds in prose).
- `.gitignore` updated so both stay **ignored** even if recreated locally.
- `trading-bot/config.example.yaml` (redacted template) is kept as the committed config.
- A clean git history was initialized here (one initial commit) — it is **push-ready**.

> The only webhook-shaped string left in the tree is a **dummy fixture** in
> `trading-bot/tests/test_discord_notifier.py` (not your live webhook — it's there so the
> formatting tests run). Safe to keep.

## 2. Prerequisites (on your Mac)

```bash
gh --version        # install: brew install gh
gh auth login       # pick GitHub.com → HTTPS or SSH → authenticate
git config --get user.name   # make sure your identity is set
```

## 3. Secret scan — run this BEFORE every push (expect NO output)

```bash
grep -rnEi 'discord(app)?\.com/api/webhooks/[0-9]+/|[0-9]{8,10}:[A-Za-z0-9_-]{30,}|AAAA[A-Za-z0-9%]{40,}' . \
  --exclude-dir=.git --exclude='*.png' \
  | grep -v 'tests/test_discord_notifier.py'
```

No lines printed = clean. Any line = stop and scrub that file before pushing.

---

## Option A — push THIS scrubbed bundle as a new repo

From inside this folder:

```bash
# creates the repo (private), adds it as 'origin', and pushes — in one shot
gh repo create pattern-strategy-bot \
  --private --source=. --remote=origin --push \
  --description "Pattern Strategy (SLC) — price-action FX trading bot (paper mode)"
```

Manual equivalent if you'd rather create it in the web UI first:

```bash
git branch -M main
git remote add origin git@github.com:<ORG-OR-USER>/pattern-strategy-bot.git
git push -u origin main
```

## Option B — you already have the REAL repo on the trading machine

Use this if your working copy (with the live `config.yaml`) is the one you want on GitHub.

```bash
cd "/path/to/your/Trading Strategy"        # the folder with the LIVE config.yaml

# 1. copy the hardened .gitignore from this bundle over yours, then:
git rm --cached trading-bot/config.yaml WEBHOOK_AND_SECRETS.md 2>/dev/null || true
#    ^ stops tracking them; the files stay on disk, just won't be pushed.

# 2. run the secret scan from section 3 — must be clean.

# 3. if those files were EVER committed before, the tokens are in history.
#    Either start a fresh history for a private repo:
rm -rf .git && git init && git add -A && git commit -m "Initial commit (scrubbed)"
#    …or scrub history with git-filter-repo / BFG if you must keep it.

# 4. create + push (private)
gh repo create pattern-strategy-bot --private --source=. --remote=origin --push
```

---

## Pre-push checklist (all must be true)

- [ ] `trading-bot/config.yaml` is **not** in the repo (only `config.example.yaml` is).
- [ ] `WEBHOOK_AND_SECRETS.md` is **not** in the repo.
- [ ] The secret scan in section 3 prints nothing.
- [ ] Visibility is **private** (or every token has been rotated if going public).
- [ ] `git status` shows no `.env`, no `state/*.log`, no `paper_ledger.json`.
- [ ] *(optional)* The canonical `CLAUDE.md` (13 safety invariants) is at repo root.

## If a token was ever pushed — rotate immediately

- **Telegram:** @BotFather → `/revoke` → issue a new token.
- **Discord:** Server Settings → Integrations → Webhooks → delete the old, create new.
- **X / Twitter:** developer portal → regenerate the bearer token.
- Put the new values only in the **un-tracked** `config.yaml` (copied from the example) or
  in environment variables — never back into git.

## After pushing

- Add collaborators / a team, and turn on branch protection on `main`.
- Point the Claude Project at this repo as the canonical hub (CLAUDE.md at root, per your
  Claude Code + Projects workflow).

---

*This repo runs in paper mode. Algorithmic trading carries real financial risk; nothing
here is financial advice.*
