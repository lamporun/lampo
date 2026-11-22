#!/usr/bin/env bash
# Blocks web traffic (ports 80/443) from countries under comprehensive EU
# sanctions, as a reasonable measure under Regulation 833/2014 art. 5n (AI
# inference via API is in scope per the Commission FAQ of 22 Jan 2026).
# SSH is never blocked. Runs at boot and weekly (lampo-geoblock.timer).
# If a download fails, the previous list stays in force.
set -euo pipefail
COUNTRIES="ru by ir kp sy cu"
DIR=/var/lib/lampo/geoblock
mkdir -p "$DIR"

fetch() { # family country
  local url
  if [ "$1" = 4 ]; then url="https://www.ipdeny.com/ipblocks/data/aggregated/$2-aggregated.zone"
  else url="https://www.ipdeny.com/ipv6/ipaddresses/aggregated/$2-aggregated.zone"; fi
  if curl -fsS --max-time 60 -o "$DIR/$2.v$1.tmp" "$url" && [ -s "$DIR/$2.v$1.tmp" ]; then
    mv "$DIR/$2.v$1.tmp" "$DIR/$2.v$1"
  else
    rm -f "$DIR/$2.v$1.tmp"
    echo "download failed for $2 IPv$1, keeping the previous list" >&2
  fi
}
for c in $COUNTRIES; do fetch 4 "$c"; fetch 6 "$c"; done

v4=$(cat "$DIR"/*.v4 2>/dev/null | grep -E '^[0-9.]+/[0-9]+$' | paste -sd, -)
v6=$(cat "$DIR"/*.v6 2>/dev/null | grep -E '^[0-9a-fA-F:]+/[0-9]+$' | paste -sd, -)
[ -n "$v4" ] || { echo "no IPv4 list available, leaving rules untouched" >&2; exit 1; }

{
  echo "table inet lampo_geo"
  echo "delete table inet lampo_geo"
  echo "table inet lampo_geo {"
  echo "  set sanctioned4 { type ipv4_addr; flags interval; auto-merge; elements = { $v4 } }"
  [ -n "$v6" ] && echo "  set sanctioned6 { type ipv6_addr; flags interval; auto-merge; elements = { $v6 } }"
  echo "  chain input {"
  echo "    type filter hook input priority -5; policy accept;"
  echo "    tcp dport { 80, 443 } ip saddr @sanctioned4 drop"
  echo "    udp dport 443 ip saddr @sanctioned4 drop"
  if [ -n "$v6" ]; then
    echo "    tcp dport { 80, 443 } ip6 saddr @sanctioned6 drop"
    echo "    udp dport 443 ip6 saddr @sanctioned6 drop"
  fi
  echo "  }"
  echo "}"
} > "$DIR/rules.nft"
nft -f "$DIR/rules.nft"
echo "geoblock active: $(echo "$v4" | tr ',' '\n' | wc -l) IPv4 and $(echo "$v6" | tr ',' '\n' | grep -c . || true) IPv6 ranges ($COUNTRIES)"
