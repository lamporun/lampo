#!/usr/bin/env bash
# Lampo: server setup for a public pi402 inference seller.
#
# Run as root on a fresh Ubuntu 24.04 x86_64 server, from a copy of this repo:
#   NETWORK=signet DOMAIN=lampo.run bash /opt/lampo/repo/deploy/setup.sh
#
# Safe to re-run (it is also how configuration changes are applied).
# Secrets live only in /etc/lampo on the server, never in this repository.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOMAIN="${DOMAIN:-lampo.run}"
NETWORK="${NETWORK:-signet}"            # signet | mainnet
GO_VERSION="1.27.3"
APERTURE_COMMIT="1f2e5a1e98adf743bc6e0765bc638d2f20c11ade"   # master, 2026-11-12
# Lampo's Lightmeter: lightmeter#8 (cached and sats-priced bundles) and
# lightmeter#9 (the usage ledger behind receipts), built against aperture#346
# through the replace in its go.mod. Both are offered upstream.
LIGHTMETER_REPO="https://github.com/lamporun/lightmeter.git"
LIGHTMETER_COMMIT="3ed1dcb196a8dd3213c1a81e9f08ab6679fcef3f" # usage-ledger, 2026-11-25
L402_RECEIPTS_COMMIT="56b984ccc164c7f44b52838424f1931021608647" # main, 2026-11-25
NODE_VERSION="v22.22.2"
WAVELENGTH_VERSION="v0.2.1"                                   # release, 2026-10-31

log() { printf '\n== %s\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "Run as root."; exit 1; }
[ "$(uname -m)" = "x86_64" ] || { echo "Only x86_64 is supported."; exit 1; }
case "$NETWORK" in signet|mainnet) ;; *) echo "NETWORK must be signet or mainnet."; exit 1 ;; esac

log "System packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git build-essential curl jq sqlite3 python3 python3-yaml ufw \
  unattended-upgrades debian-keyring debian-archive-keyring apt-transport-https gnupg \
  rsync openssl nftables >/dev/null
if ! command -v caddy >/dev/null 2>&1; then
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
    | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq
  apt-get install -y -qq caddy >/dev/null
fi
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF

log "Swap (small VPS: compiling Go needs more than 2 GB of RAM at peak)"
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

log "SSH: keys only"
cat > /etc/ssh/sshd_config.d/10-lampo.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
EOF
systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true

log "Firewall"
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow 22/tcp >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw allow 443/udp >/dev/null
ufw --force enable >/dev/null

log "Go $GO_VERSION"
if ! /usr/local/go/bin/go version 2>/dev/null | grep -q "go$GO_VERSION "; then
  tarball="go$GO_VERSION.linux-amd64.tar.gz"
  want=$(curl -fsSL "https://go.dev/dl/?mode=json&include=all" \
    | jq -r --arg f "$tarball" '[.[].files[] | select(.filename==$f)][0].sha256')
  [ -n "$want" ] && [ "$want" != "null" ] || { echo "Go $GO_VERSION not found on go.dev"; exit 1; }
  curl -fsSL -o "/tmp/$tarball" "https://go.dev/dl/$tarball"
  got=$(sha256sum "/tmp/$tarball" | awk '{print $1}')
  [ "$want" = "$got" ] || { echo "Go checksum mismatch"; exit 1; }
  rm -rf /usr/local/go
  tar -C /usr/local -xzf "/tmp/$tarball"
  rm -f "/tmp/$tarball"
fi
export PATH="/usr/local/go/bin:$PATH" GOPATH=/opt/lampo/go GOCACHE=/opt/lampo/gocache GOTOOLCHAIN=local

log "Aperture and Lightmeter (pinned commits)"
install -d -m 755 /opt/lampo/src /opt/lampo/bin
fetch() {
  local dir="/opt/lampo/src/$1"
  [ -d "$dir/.git" ] || git clone -q "${3:-https://github.com/lightninglabs/$1.git}" "$dir"
  git -C "$dir" fetch -q origin
  git -C "$dir" checkout -q -f --detach "$2"
  git -C "$dir" clean -qfd
}
fetch aperture "$APERTURE_COMMIT"
(cd /opt/lampo/src/aperture && go build -trimpath -o /usr/local/bin/aperture ./cmd/aperture)
fetch lightmeter-lampo "$LIGHTMETER_COMMIT" "$LIGHTMETER_REPO"
(cd /opt/lampo/src/lightmeter-lampo && CGO_ENABLED=0 go build -trimpath -o /usr/local/bin/lightmeterd ./cmd/lightmeterd)

log "Node.js $NODE_VERSION (official tarball, checked against SHASUMS256.txt)"
if [ "$(/opt/node/current/bin/node --version 2>/dev/null)" != "$NODE_VERSION" ]; then
  tarball="node-$NODE_VERSION-linux-x64.tar.xz"
  curl -fsSL -o "/tmp/$tarball" "https://nodejs.org/dist/$NODE_VERSION/$tarball"
  curl -fsSL "https://nodejs.org/dist/$NODE_VERSION/SHASUMS256.txt" | grep " $tarball\$" > /tmp/node.sha256
  (cd /tmp && sha256sum -c node.sha256)
  install -d -m 755 /opt/node
  tar -C /opt/node -xJf "/tmp/$tarball"
  ln -sfn "/opt/node/node-$NODE_VERSION-linux-x64" /opt/node/current
  rm -f "/tmp/$tarball" /tmp/node.sha256
fi

log "l402-receipts (signed usage receipts, its own user and sandbox)"
id receipts >/dev/null 2>&1 || useradd --system --no-create-home --home-dir /nonexistent \
  --shell /usr/sbin/nologin --user-group receipts
usermod -a -G lampo receipts
fetch l402-receipts "$L402_RECEIPTS_COMMIT" "https://github.com/lamporun/l402-receipts.git"
rsync -a --delete --exclude .git /opt/lampo/src/l402-receipts/ /opt/l402-receipts/
(cd /opt/l402-receipts && PATH="/opt/node/current/bin:$PATH" npm ci --omit=dev --no-audit --no-fund --ignore-scripts)
chown -R root:root /opt/l402-receipts
install -d -m 700 -o receipts -g receipts /etc/l402-receipts
install -d -m 755 -o receipts -g receipts /var/lib/l402-receipts /var/lib/l402-receipts/anchors
# The signing key is made on the server and never leaves it.
[ -f /etc/l402-receipts/key ] || sudo -u receipts env NODE_NO_WARNINGS=1 /opt/node/current/bin/node \
  /opt/l402-receipts/bin/l402-receipts.js keygen --out /etc/l402-receipts/key
[ -f /etc/l402-receipts/env ] || { printf 'PUBLIC_URL=https://%s\nRECEIPTS_SINCE=%s\nRELAY_ARGS=--relay wss://nos.lol --relay wss://relay.primal.net --relay wss://relay.damus.io\n' \
  "$DOMAIN" "$(date -u +%F)" > /etc/l402-receipts/env; chown receipts:receipts /etc/l402-receipts/env; chmod 600 /etc/l402-receipts/env; }

log "Wavelength $WAVELENGTH_VERSION (release binaries, checksum from the signed manifest)"
if [ "$(cat /opt/lampo/.wavelength-version 2>/dev/null)" != "$WAVELENGTH_VERSION" ]; then
  name="wavelength-linux-amd64-$WAVELENGTH_VERSION"
  base="https://github.com/lightninglabs/wavelength/releases/download/$WAVELENGTH_VERSION"
  tmp=$(mktemp -d)
  curl -fsSL -o "$tmp/$name.tar.gz" "$base/$name.tar.gz"
  curl -fsSL -o "$tmp/manifest.txt" "$base/manifest-$WAVELENGTH_VERSION.txt"
  want=$(awk -v f="$name.tar.gz" '$2==f || $2=="*"f {print $1}' "$tmp/manifest.txt")
  got=$(sha256sum "$tmp/$name.tar.gz" | awk '{print $1}')
  [ -n "$want" ] && [ "$want" = "$got" ] || { echo "Wavelength checksum mismatch"; exit 1; }
  tar -C "$tmp" -xzf "$tmp/$name.tar.gz"
  install -m 755 "$(find "$tmp" -name waved -type f | head -1)" /usr/local/bin/waved
  install -m 755 "$(find "$tmp" -name wavecli -type f | head -1)" /usr/local/bin/wavecli
  rm -rf -- "${tmp:?}"
  echo "$WAVELENGTH_VERSION" > /opt/lampo/.wavelength-version
fi

log "User and folders"
id lampo >/dev/null 2>&1 || useradd --system --home-dir /var/lib/lampo --create-home \
  --shell /usr/sbin/nologin lampo
WAVED_DIR="/var/lib/lampo/waved-$NETWORK"
for d in /var/lib/lampo /var/lib/lampo/aperture /var/lib/lampo/lightmeter \
         /var/lib/lampo/ledger /var/lib/lampo/partners /var/lib/lampo/guard "$WAVED_DIR"; do
  install -d -o lampo -g lampo -m 750 "$d"
done
install -d -o root -g lampo -m 750 /etc/lampo
install -d -m 755 /srv/lampo
install -d -o lampo -g lampo -m 755 /srv/lampo/site

log "Secrets (created once, edited by hand on the server)"
umask 027
[ -f /etc/lampo/baseten.env ] || printf 'BASETEN_API_KEY=\n' > /etc/lampo/baseten.env
[ -f /etc/lampo/lightmeter.secrets ] || printf 'RECON_TOKEN=%s\nWEBHOOK_SECRET=%s\n' \
  "$(openssl rand -hex 32)" "$(openssl rand -hex 32)" > /etc/lampo/lightmeter.secrets
[ -f /etc/lampo/alert.env ] || printf 'TELEGRAM_TOKEN=\nTELEGRAM_CHAT_ID=\n' > /etc/lampo/alert.env
[ -f /etc/lampo/sweep.env ] || printf 'SWEEP_ENABLED=0\nSWEEP_LN_ADDRESS=\nSWEEP_THRESHOLD_SAT=150000\nSWEEP_KEEP_SAT=20000\n' \
  > /etc/lampo/sweep.env
[ -f /etc/lampo/launch.env ] || printf 'LAUNCH_TS=0\n' > /etc/lampo/launch.env
# Partner program: commission on bundles bought through partner links, paid
# out to the partner's Lightning address (scripts/partners_*.py).
[ -f /etc/lampo/guardbot.env ] || printf 'GUARDBOT_TOKEN=\nGUARDBOT_OPERATOR_CHAT=\n' > /etc/lampo/guardbot.env
[ -f /etc/lampo/partners.env ] || printf 'PARTNER_COMMISSION_PERCENT=10\nPARTNER_PAYOUT_THRESHOLD_SAT=1000\nPARTNER_DAILY_PAYOUT_CAP_SAT=20000\nPARTNER_PAYOUTS_ENABLED=1\n' \
  > /etc/lampo/partners.env
printf 'LAMPO_NETWORK=%s\nWAVED_DIR=%s\nDOMAIN=%s\n' "$NETWORK" "$WAVED_DIR" "$DOMAIN" \
  > /etc/lampo/network.env
umask 022

log "Configuration"
# shellcheck disable=SC1091
. /etc/lampo/lightmeter.secrets
render() {
  sed -e "s|@DOMAIN@|$DOMAIN|g" -e "s|@NETWORK@|$NETWORK|g" -e "s|@WAVED_DIR@|$WAVED_DIR|g" \
      -e "s|@RECON_TOKEN@|$RECON_TOKEN|g" -e "s|@WEBHOOK_SECRET@|$WEBHOOK_SECRET|g" "$1"
}
render "$REPO_DIR/deploy/aperture.yaml" > /etc/lampo/aperture.yaml
render "$REPO_DIR/deploy/lightmeter.yaml" > /etc/lampo/lightmeter.yaml
if [ "${DOMAIN#:}" != "$DOMAIN" ]; then
  # Test mode before the domain points here (DOMAIN=":80"): plain HTTP on the
  # server's IP, no certificate, no www redirect.
  render "$REPO_DIR/deploy/Caddyfile" | sed '/^www\./,/^}/d' > /etc/caddy/Caddyfile
else
  render "$REPO_DIR/deploy/Caddyfile" > /etc/caddy/Caddyfile
fi
chown root:lampo /etc/lampo/*
chmod 640 /etc/lampo/*

log "Logs: kept at most 14 days"
install -d /etc/systemd/journald.conf.d
install -m 644 "$REPO_DIR/deploy/journald/10-lampo.conf" /etc/systemd/journald.conf.d/10-lampo.conf
systemctl restart systemd-journald

log "Scripts, site and services"
install -m 755 "$REPO_DIR"/scripts/*.sh "$REPO_DIR"/scripts/*.py "$REPO_DIR"/scripts/wcli /opt/lampo/bin/
# stats.json and models.json are rewritten every 10 minutes by lampo-stats.
rsync -a --delete --exclude stats.json --exclude models.json "$REPO_DIR/site/" /srv/lampo/site/
[ -f /srv/lampo/site/stats.json ] || cp "$REPO_DIR/site/stats.json" /srv/lampo/site/stats.json
[ -f /srv/lampo/site/models.json ] || cp "$REPO_DIR/site/models.json" /srv/lampo/site/models.json
chown -R lampo:lampo /srv/lampo/site
install -m 644 "$REPO_DIR"/deploy/systemd/* /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now waved.service lightmeterd.service caddy.service
# Restart the wallet too: network.env may have changed (signet <-> mainnet).
systemctl restart waved.service lightmeterd.service caddy.service
systemctl enable --now lampo-stats.timer lampo-sweep.timer lampo-daily.timer lampo-geoblock.timer lampo-watch.timer lampo-health.timer
systemctl enable lampo-geoblock.service
systemctl enable --now lampo-tgbot.service lampo-balance.service lampo-partners.service lampo-partners-worker.timer
systemctl enable --now l402-receipts.service l402-receipts-anchor.timer
grep -q '^GUARDBOT_TOKEN=.\+' /etc/lampo/guardbot.env && systemctl enable --now lampo-guardbot.service
systemctl start lampo-geoblock.service || echo "Warning: geoblock failed, see journalctl -u lampo-geoblock"

if [ -f "$WAVED_DIR/aperture-recv.macaroon" ]; then
  systemctl enable aperture.service
  systemctl restart aperture.service
else
  # No wallet for this network yet: Aperture must not mint invoices from a
  # stale config.
  systemctl stop aperture.service 2>/dev/null || true
  echo
  echo "Wallet not created yet for $NETWORK. In your own terminal run:"
  echo "  ssh -t lampo sudo /opt/lampo/bin/create-wallet.sh"
fi
grep -q '^BASETEN_API_KEY=.\+' /etc/lampo/baseten.env || \
  echo "Reminder: put the Baseten key in /etc/lampo/baseten.env (sudo nano), then: systemctl restart aperture"
log "Done ($NETWORK, https://$DOMAIN)"
