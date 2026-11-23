# Post per Reddit (pronti da copiare)

Uno al giorno, non tutti insieme. Rispondi ai commenti nelle prime ore. Se un post viene rimosso
(account nuovo, poco karma), scrivi gentilmente ai moderatori del subreddit.

## r/lightningnetwork (oggi)

**Titolo:** I built a pay-per-token AI API on Lightning: no account, no API key, open source (L402)

Hi all. Lampo (https://lampo.run) sells access to top open-weight models (Kimi K3, GLM 5.x, DeepSeek V4 Flash) through an OpenAI-compatible API, paid per token over Lightning.

How it works: your request gets an HTTP 402 with a Lightning invoice. You pay it (1,000 sats buys 1M tokens of DeepSeek V4 Flash), retry with the preimage, and the answer streams back. The same credential works until the bundle runs out. No signup, no card, no KYC.

It's also the first public seller for pi402, the Lightning Labs extension that lets the Pi coding agent pay for its own inference.

Video of a real purchase (23 s): https://lampo.run/demo.mp4

Stack: Aperture + Lightmeter + a Wavelength wallet (no node, no channels) on one small VPS. The whole setup is open source if you want to run your own seller: https://github.com/lamporun/lampo

Things I learned the hard way:
- On mainnet the Wavelength wallet refuses to receive less than 1,000 sats, so my cheapest bundle silently broke on launch day.
- A payment fails if it would leave 1–999 sats of change: buyers should keep 2,000+ sats.
- Prices are whole millisats per token, so nothing can cost less than about $0.61 per million tokens.

Launched two days ago, no external sales yet. Feedback very welcome, especially from anyone running L402 services.

## r/SideProject (domani)

**Titolo:** Lampo: top AI models paid in sats. No card, no account, no API key

I built Lampo, an OpenAI-compatible API for Kimi, GLM and DeepSeek models that you pay per use with Bitcoin over the Lightning Network.

Why: in a lot of countries getting a card that works with US AI providers is a pain, and AI agents can't open accounts or type card numbers at all. With Lampo your code (or your agent) gets an invoice, pays it, and gets the answer. 1,000 sats (about $0.60) buys a million tokens of the cheapest model.

- Live: https://lampo.run (prices update every 10 minutes)
- 23-second video of a real purchase: https://lampo.run/demo.mp4
- Open source: https://github.com/lamporun/lampo

Two days old, zero customers so far, so I'd really like honest feedback: would you use something like this, and what would stop you?

## r/AI_Agents (leggi prima le regole: spesso vogliono i progetti nel thread settimanale) (dopodomani)

**Titolo:** Letting agents pay for their own LLM calls over Lightning, no API keys

One problem with autonomous agents: someone has to create the account and hold the API key. I've been running a different setup: an OpenAI-compatible endpoint that answers HTTP 402 with a Lightning invoice. The agent's wallet pays it within a spending limit, retries with the proof of payment (L402), and the answer streams back. One payment buys a token bundle that the same credential keeps using.

It works today with pi402 (an extension for the Pi coding agent) and with any L402 client. Models: Kimi K3, GLM 5.x, DeepSeek V4 Flash; from 1,000 sats (about $0.60) for a million tokens.

Live: https://lampo.run · real purchase in 23 s: https://lampo.run/demo.mp4 · open source: https://github.com/lamporun/lampo

Curious how others handle payments and keys for agents that run unattended.
