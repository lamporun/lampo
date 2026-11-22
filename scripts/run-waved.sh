#!/bin/sh
# Starts the Wavelength wallet daemon for the network in /etc/lampo/network.env.
# Auto-unlock is enabled only once the wallet exists (create-wallet.sh).
set -eu
. /etc/lampo/network.env
set -- --network="$LAMPO_NETWORK" --datadir="$WAVED_DIR" --wallet.type=lwwallet \
  --rpc.listenaddr=127.0.0.1:10029 \
  --rpc.gateway.enabled --rpc.gateway.listenaddr=127.0.0.1:10061
if [ "$LAMPO_NETWORK" = "mainnet" ]; then
  set -- "$@" --allow-mainnet
fi
if [ -f "$WAVED_DIR/.wallet-created" ]; then
  set -- "$@" --wallet.password_file=/etc/lampo/wallet.password
fi
exec /usr/local/bin/waved "$@"
