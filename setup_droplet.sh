#!/usr/bin/env bash
# One-shot installer for the Playtomic watcher on an Ubuntu server (e.g. a
# DigitalOcean droplet). Installs Python + the watcher, stores your secrets in a
# root-only env file, and runs it as a 60-second background service that
# restarts on crash/reboot. READ ONLY -- only reads pages and sends Telegram.
set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/mattsayer17-sys/playtomic-watch/main"
APP_DIR="/opt/playtomic-watch"
ENV_FILE="$APP_DIR/watch.env"

echo "== Installing system packages =="
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip curl >/dev/null

echo "== Fetching the watcher =="
mkdir -p "$APP_DIR"
curl -fsSL "$REPO_RAW/playtomic_watch.py" -o "$APP_DIR/playtomic_watch.py"

echo "== Python environment =="
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip requests

echo
echo "== Paste your 3 secrets (right-click or Ctrl+Shift+V to paste) =="
read -rp "Telegram bot token: " TG_TOKEN
read -rp "Telegram chat id:   " TG_CHAT
read -rp "Proxy URL (http://user:pass@host:port): " PROXY

umask 077
cat > "$ENV_FILE" <<EOF
TELEGRAM_BOT_TOKEN=$TG_TOKEN
TELEGRAM_CHAT_ID=$TG_CHAT
PLAYTOMIC_PROXY=$PROXY
EOF
chmod 600 "$ENV_FILE"

echo "== Proxy reachability test =="
if curl -s -o /dev/null -w "%{http_code}" -x "$PROXY" \
     https://playtomic.com/clubs/the-padel-hub-kt19-epsom | grep -q 200; then
  echo "  proxy -> Playtomic: 200 OK"
else
  echo "  WARNING: proxy did not return 200 for Playtomic. Check the proxy URL."
fi

echo "== Installing background service =="
cat > /etc/systemd/system/playtomic-watch.service <<EOF
[Unit]
Description=Playtomic free-place watcher (PADELHUB KT19 Epsom)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=$ENV_FILE
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/python -u $APP_DIR/playtomic_watch.py watch \\
  --match "King of the Court (ALL LEVELS)" --match "PADELHUB Social" \\
  --min-level 3.01 --max-level 3.01 --interval 60 \\
  --state $APP_DIR/playtomic_state.json
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now playtomic-watch.service
sleep 3
echo
echo "== Service status =="
systemctl --no-pager --full status playtomic-watch.service | head -15
echo
echo "Done. Live logs:  journalctl -u playtomic-watch -f"
