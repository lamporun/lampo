#!/usr/bin/env bash
# Lampo health check (root, every 5 minutes): alerts on Telegram when a service
# is down or when Aperture cannot create invoices (as on 2026-11-22, when the
# wallet refused sub-1,000-sat receives for hours and nobody noticed).
# At most one alert per problem per hour.
set -uo pipefail
state=/var/lib/lampo/health-alerts; mkdir -p "$state"
alert_once() {  # key, message
  local f="$state/$1"
  if [ ! -f "$f" ] || [ $(( $(date +%s) - $(stat -c %Y "$f") )) -ge 3600 ]; then
    /opt/lampo/bin/alert.sh "$2"; touch "$f"
  fi
}
for s in caddy aperture lightmeterd waved lampo-tgbot lampo-balance lampo-partners guard-cloud; do
  if ! systemctl is-active --quiet "$s"; then
    alert_once "down-$s" "Lampo: il servizio $s è fermo. Provo a riavviarlo."
    systemctl restart "$s" || true
  fi
done
n=$(journalctl -u aperture --since "-5 min" --no-pager -o cat 2>/dev/null | grep -cE "Error adding invoice|Error minting L402|challenge generation failed")
if [ "${n:-0}" -gt 0 ]; then
  why=$(journalctl -u aperture --since "-5 min" --no-pager -o cat | grep -m1 -oE "wallet recv failed.{0,120}")
  alert_once invoices "Lampo: le fatture non vengono create ($n errori negli ultimi 5 minuti). I clienti non possono pagare. Dettaglio: ${why:-vedi journalctl -u aperture}"
fi
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 https://lampo.run/ || echo 000)
[ "$code" = "200" ] || alert_once site "Lampo: il sito risponde $code invece di 200."
exit 0
