# Lampo

A public [pi402](https://pi402.tech/) seller: the Pi coding agent (with pi402), or any agent with an L402 wallet, buys AI inference over the Bitcoin Lightning Network, per use, with no account, no API key and no card.

Stack (all MIT, by Lightning Labs), on one small Linux server:

- **Aperture**: an L402 + MPP paywall in front of Baseten Model APIs.
- **Lightmeter**: prices, prepaid token bundles, a usage ledger.
- **Wavelength**: a self-custodial wallet that mints invoices, with no Lightning node or channels.
- **Caddy**: TLS and the website.

## Layout
- `deploy/setup.sh`: idempotent server setup (Ubuntu 24.04, x86_64).
- `deploy/aperture.yaml`, `deploy/lightmeter.yaml`, `deploy/Caddyfile`: config templates.
- `deploy/systemd/`: services and timers.
- `scripts/`: wallet helpers, sweep to a Lightning address, stats/ledger, alerts.
- `site/`: the website (instructions, live prices and counter, terms, privacy).

Secrets never live in this repository: they are created on the server in `/etc/lampo`.

## Run your own seller

1. A fresh Ubuntu 24.04 x86_64 server with ports 22, 80 and 443, and a domain pointing at it.
2. Copy this repository to `/opt/lampo/repo`, then run `NETWORK=signet DOMAIN=your.domain bash /opt/lampo/repo/deploy/setup.sh` as root.
3. Put your Baseten API key in `/etc/lampo/baseten.env`, create the wallet with `scripts/create-wallet.sh` (write the recovery words on paper), and test with pi402 on signet.
4. Re-run setup with `NETWORK=mainnet`, publish your own terms and privacy notice, and set the sweep address in `/etc/lampo/sweep.env`.

Edit `site/` (operator details, terms, privacy) and `deploy/lightmeter.yaml` (models, margin) before going live: they describe Lampo, not you.

## Lessons from running it

- **pi402's default cap is 1,000 sats per payment.** Size at least one bundle below it, or buyers must raise `PI402_MAX_SAT_PER_REQUEST`.
- **Wavelength refuses sends and change below 1,000 sats** unless you allow credit, and a mainnet account holds at most 300,000 sats: sweep often, from confirmed coins.
- **Rates are whole msat per token.** At BTC ≈ $60k that is a floor of about $0.61 per million tokens, so very cheap models are sold above their real price.
- **Cached input tokens are billed as fresh input** by today's metering, although upstreams charge a fraction for them.
- **Unknown model ids fail closed** (HTTP 500): no free service, but publish your model ids (`/v1/models`, `llms.txt`).
- **Streaming needs an unbuffered proxy** (`flush_interval -1` in Caddy).

Contributions upstream: `contrib/` holds the patches we sent to pi-402 and pi402.tech.
