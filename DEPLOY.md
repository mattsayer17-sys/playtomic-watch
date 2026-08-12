# Deploy the 24/7 watcher on GitHub Actions (no laptop needed)

Runs in GitHub's cloud every ~5 min and pushes Telegram alerts when a spot
opens on **King of the Court (ALL LEVELS)** or **PADELHUB Social** at your
level (3.01). Works with your laptop closed and phone in your pocket.

## Steps (browser only, ~2 min)

1. **Create the repo:** go to https://github.com/new
   - Name: `playtomic-watch`
   - Visibility: **Public**  (public = unlimited free Actions minutes; a 5-min
     cron exceeds the private-repo free allowance)
   - Click **Create repository**.

2. **Upload the files:** on the new empty repo page click
   **"uploading an existing file"**, then drag in from
   `C:\Users\matts\OneDrive\Documents\playtomic-watch`:
   - `playtomic_watch.py`
   - `requirements.txt`
   - the whole **`.github`** folder (drag the folder; GitHub keeps the
     `.github/workflows/watch.yml` path)

   Do **NOT** upload `.venv`, `playtomic_state.json`, or `DEPLOY.md`.
   Click **Commit changes**.

3. **Add your Telegram secrets:** repo **Settings -> Secrets and variables ->
   Actions -> New repository secret**. Add two:
   - `TELEGRAM_BOT_TOKEN`  = your bot token
   - `TELEGRAM_CHAT_ID`    = 8722645806

4. **Turn it on / test now:** open the **Actions** tab, click the
   **playtomic-watch** workflow, then **Run workflow** (the manual button).
   First run just records the baseline (no alert). After that it alerts on any
   0 -> positive change. Done — it now runs itself every ~5 min.

## Latency note
GitHub's cron minimum is 5 min and scheduled runs can lag a few more minutes
under load. If you need to beat other players to a freed spot reliably, a small
always-on VPS or home device polling every 30-60s is faster — ask me to set
that up and I'll generate the systemd service.

## Change what's watched
Edit the `--match` / `--min-level` / `--max-level` args in
`.github/workflows/watch.yml`.
