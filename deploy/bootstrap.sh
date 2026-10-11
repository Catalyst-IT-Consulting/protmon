#!/usr/bin/env bash
# One-time provisioning for the display Pi.
#
# Run this once, as root, while you still have a keyboard or an SSH session on
# the box. After it finishes, every further change arrives by git push.
#
#   sudo ./bootstrap.sh \
#     --repo git@github.com:Catalyst-IT-Consulting/protmon.git \
#     --key  /path/to/deploy_key \
#     [--ref release]
#
# The key is a read-only GitHub deploy key for this repo. Generate it with
# ssh-keygen on the admin machine, add the public half under the repo's Deploy
# keys with write access off, and hand this script the private half.
set -euo pipefail

REPO=""
KEY=""
REF=release
APP_DIR=${APP_DIR:-/opt/protmon}
ETC_DIR=/etc/protmon
STATE_DIR=/var/lib/protmon
LOG_DIR=/var/log/protmon
SSH_KEY_DEST=/root/.ssh/protmon-deploy

while [ $# -gt 0 ]; do
  case $1 in
    --repo) REPO=$2; shift 2 ;;
    --key)  KEY=$2; shift 2 ;;
    --ref)  REF=$2; shift 2 ;;
    *) printf 'unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done

[ "$(id -u)" = 0 ] || { printf 'must run as root\n' >&2; exit 1; }
[ -n "$REPO" ] || { printf '--repo is required\n' >&2; exit 2; }

say() { printf '\n==> %s\n' "$*"; }

say "Installing packages"
apt-get update -qq
apt-get install -y -qq git curl python3 python3-venv ca-certificates logrotate

say "Creating directories"
install -d -m 0755 "$ETC_DIR" "$STATE_DIR" "$LOG_DIR"

if [ -n "$KEY" ]; then
  say "Installing deploy key"
  install -d -m 0700 /root/.ssh
  install -m 0600 "$KEY" "$SSH_KEY_DEST"
  ssh-keyscan -t ed25519,rsa github.com 2>/dev/null >>/root/.ssh/known_hosts
  sort -u /root/.ssh/known_hosts -o /root/.ssh/known_hosts
  if ! grep -q "$SSH_KEY_DEST" /root/.ssh/config 2>/dev/null; then
    cat >>/root/.ssh/config <<SSHCFG

Host github.com
  IdentityFile $SSH_KEY_DEST
  IdentitiesOnly yes
SSHCFG
    chmod 600 /root/.ssh/config
  fi
fi

if [ -d "$APP_DIR/.git" ]; then
  say "Repository already present at $APP_DIR, fetching"
  git -C "$APP_DIR" remote set-url origin "$REPO"
  git -C "$APP_DIR" fetch --prune origin "$REF"
  git -C "$APP_DIR" checkout -B "$REF" "origin/$REF"
else
  say "Cloning $REPO"
  git clone --branch "$REF" "$REPO" "$APP_DIR"
fi
git config --global --add safe.directory "$APP_DIR"

if [ ! -f "$ETC_DIR/deploy.env" ]; then
  say "Writing $ETC_DIR/deploy.env"
  install -m 0644 "$APP_DIR/deploy/deploy.env.example" "$ETC_DIR/deploy.env"
  sed -i "s/^DEPLOY_REF=.*/DEPLOY_REF=$REF/" "$ETC_DIR/deploy.env"
else
  say "Keeping existing $ETC_DIR/deploy.env"
fi

say "Installing system overlay"
export APP_DIR STATE_DIR
"$APP_DIR/deploy/install-system.sh"

say "Enabling the deploy timer"
systemctl enable --now protmon-deploy.timer

cat <<DONE

Bootstrap complete. The Pi now tracks '$REF' and applies it within a minute of
a push.

Still to do by hand on this box, once:

  1. $ETC_DIR/config.json       cameras and alerts, holds RTSP credentials
  2. $ETC_DIR/secrets.env       PROTMON_TOKEN and PROTMON_ALLOWED_IPS
  3. Static IP or DHCP reservation, so Alarm Manager has a stable target
  4. Firewall: webhook port inbound from the Cloud Key, SSH from admin only

To pause automatic deploys while troubleshooting:

  sudo touch $ETC_DIR/HOLD      # deploys stop, display keeps running
  sudo rm $ETC_DIR/HOLD         # deploys resume

To apply a push immediately instead of waiting for the timer:

  sudo /usr/local/bin/protmon-update --force
DONE
