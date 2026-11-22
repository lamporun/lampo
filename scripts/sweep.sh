#!/usr/bin/env bash
# Moves earnings above a threshold from Lampo's Wavelength wallet to the operator's
# Lightning address (LNURL-pay), keeping the alpha wallet's balance low.
# Settings: /etc/lampo/sweep.env. With SWEEP_ENABLED=0 it only reports.
set -euo pipefail
# shellcheck disable=SC1091
. /etc/lampo/sweep.env
W=/opt/lampo/bin/wcli
LOG=/var/lib/lampo/ledger/sweeps.log
threshold=${SWEEP_THRESHOLD_SAT:-150000}
keep=${SWEEP_KEEP_SAT:-20000}

bal=$("$W" --json balance)
coins=$(jq -r '(.confirmed_sat // 0) | tonumber' <<<"$bal")
credit=$(jq -r '(.credit_available_sat // 0) | tonumber' <<<"$bal")
avail=$((coins + credit))
echo "balance: coins=$coins credit=$credit total=$avail threshold=$threshold"

if [ "$avail" -lt "$threshold" ]; then exit 0; fi
if [ "${SWEEP_ENABLED:-0}" != "1" ]; then
  /opt/lampo/bin/alert.sh "Saldo $avail sats sopra la soglia, ma lo sweep è spento (SWEEP_ENABLED=0)."
  exit 0
fi
[ -n "${SWEEP_LN_ADDRESS:-}" ] || { echo "SWEEP_LN_ADDRESS is empty"; exit 1; }

# Sweep Ark coins only: credit is redeemed into coins by the wallet itself.
# Wavelength refuses a payment whose change falls below 1,000 sats (operator
# VTXO minimum), so always leave at least 2,000 sats of change for the fee.
[ "$keep" -ge 2000 ] || keep=2000
amount=$((coins - keep))
[ "$amount" -gt 0 ] || { echo "not enough coins to sweep (coins=$coins keep=$keep)"; exit 0; }
user=${SWEEP_LN_ADDRESS%@*}
host=${SWEEP_LN_ADDRESS#*@}
meta=$(curl -fsS --max-time 20 "https://$host/.well-known/lnurlp/$user")
callback=$(jq -r '.callback' <<<"$meta")
min_msat=$(jq -r '.minSendable' <<<"$meta")
max_msat=$(jq -r '.maxSendable' <<<"$meta")
msat=$((amount * 1000))
[ "$msat" -le "$max_msat" ] || msat=$max_msat
[ "$msat" -ge "$min_msat" ] || { echo "amount below the address minimum"; exit 0; }
sep='?'; case "$callback" in *\?*) sep='&' ;; esac
pr=$(curl -fsS --max-time 20 "${callback}${sep}amount=${msat}" | jq -r '.pr')
case "$pr" in ln*) ;; *) echo "no invoice from $SWEEP_LN_ADDRESS"; exit 1 ;; esac

# Pay from coins only (no credit), confirmed non-interactively.
flags=${SWEEP_SEND_FLAGS:-"--yes"}
# shellcheck disable=SC2086
res=$("$W" --json send "$pr" $flags)
printf '%s %s sats -> %s %s\n' "$(date -u +%FT%TZ)" "$((msat / 1000))" "$SWEEP_LN_ADDRESS" \
  "$(jq -c '{status, amount_sat, fee_sat}' <<<"$res" 2>/dev/null || echo "$res")" >> "$LOG"
/opt/lampo/bin/alert.sh "Sweep: $((msat / 1000)) sats inviati a $SWEEP_LN_ADDRESS"
