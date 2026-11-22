#!/usr/bin/env bash
# Creates Lampo's Wavelength wallet. The operator runs it themselves, in their own terminal,
# so the recovery words never pass through any chat or log:
#   ssh -t lampo sudo /opt/lampo/bin/create-wallet.sh
set -euo pipefail
. /etc/lampo/network.env
[ "$(id -u)" -eq 0 ] || { echo "Usa: sudo $0"; exit 1; }

if [ -f "$WAVED_DIR/.wallet-created" ]; then
  echo "Il wallet per la rete $LAMPO_NETWORK esiste già. Niente da fare."
  exit 0
fi

# Random password for auto-unlock after reboots. Nobody needs to remember it:
# the recovery words are what restores the funds.
if [ ! -s /etc/lampo/wallet.password ]; then
  ( umask 027; openssl rand -base64 32 > /etc/lampo/wallet.password )
  chown root:lampo /etc/lampo/wallet.password
  chmod 640 /etc/lampo/wallet.password
fi

cat <<EOF

  WALLET DI LAMPO - rete: $LAMPO_NETWORK

  Tra poco compariranno le PAROLE DI RECUPERO, una volta sola.
  - Scrivile su carta, a mano, in ordine.
  - Non fotografarle, non copiarle sul computer, non incollarle in nessuna chat.
  - Chi ha quelle parole ha i soldi del wallet.

EOF
read -r -p "Premi Invio quando hai carta e penna pronte... " _

sudo -u lampo /opt/lampo/bin/wcli create --wallet-password-file=/etc/lampo/wallet.password

echo
answer=""
while [ "$answer" != "SI" ]; do
  read -r -p "Hai scritto tutte le parole su carta? Scrivi SI per continuare: " answer
done
printf '\033c'   # clear the screen so the words do not stay on it

touch "$WAVED_DIR/.wallet-created"
chown lampo:lampo "$WAVED_DIR/.wallet-created"

echo "Creo la credenziale che permette ad Aperture SOLO di creare fatture..."
sudo -u lampo /opt/lampo/bin/wcli bakemacaroon \
  --save-to "$WAVED_DIR/aperture-recv.macaroon" uri:/wavewalletrpc.WalletService/Recv >/dev/null

echo "Riavvio il wallet con lo sblocco automatico..."
systemctl restart waved.service
for _ in $(seq 1 30); do
  if sudo -u lampo /opt/lampo/bin/wcli getinfo 2>/dev/null | grep -q WALLET_STATE_READY; then break; fi
  sleep 2
done
sudo -u lampo /opt/lampo/bin/wcli getinfo | grep -E 'wallet_state|server_connected' || true

systemctl enable aperture.service
systemctl restart aperture.service
echo
echo "Fatto. Il wallet è pronto e Aperture è partito. Puoi chiudere questo terminale."
