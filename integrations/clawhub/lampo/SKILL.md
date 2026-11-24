---
name: lampo
description: Call open-weight AI models (DeepSeek V4 Flash, GLM 5.3 Flash, GLM 5.2, GLM 5.3, Kimi K3, Kimi K3.1 Pro) through Lampo, an OpenAI-compatible API paid per token in sats over Lightning (L402), with no account or API key. Use when the user wants a second opinion from another model, wants to hand a bulk or low-stakes subtask (summarize, translate, classify, extract, draft) to a cheap model, or wants these models without signing up anywhere. Pays from the user's own Lightning wallet through @getalby/cli (Nostr Wallet Connect).
metadata:
  openclaw:
    emoji: "⚡"
    homepage: https://lampo.run
    primaryEnv: NWC_URL
    requires:
      env:
        - NWC_URL
      bins:
        - npx
---

# Lampo: AI models paid in sats

Lampo (https://lampo.run) sells chat completions in the OpenAI format at
`https://lampo.run/v1/chat/completions`. There is no account and no API key:
the first request returns `402 Payment Required` with a Lightning invoice for a
prepaid **bundle of tokens**, and the paid credential then works for many
requests until the bundle is used up.

Payments use the Alby CLI, `@getalby/cli`, with the user's wallet connection in
`NWC_URL` (or a wallet saved earlier with `npx @getalby/cli connect`).

## Cost: read this first

- This skill spends real bitcoin from the user's wallet. **Ask the user before
  the first purchase in a session**, and tell them the price.
- Always pass `--max-amount` so a request can never pay more than you agreed.
- Bundles have fixed prices: 1,000 sats (DeepSeek V4 Flash, about 2.8M tokens;
  GLM 5.3 Flash, about 1.7M), 1,500 (Nemotron), 2,500 (GLM 5.2/5.3, DeepSeek V4
  Pro), 3,000 sats (Kimi K3 and Kimi K3.1 Pro). Prices follow the BTC price. Live
  prices: https://lampo.run/stats.json
- Payments are final. Answers come from AI models and can be wrong.

## Models

| Model id (send this in `model`) | Good for |
|---|---|
| `deepseek-ai/DeepSeek-V4-Flash-0731` | cheapest; bulk and simple tasks |
| `zai-org/GLM-5.3-Flash` | cheap; also reads images |
| `zai-org/GLM-5.2`, `zai-org/GLM-5.3` | stronger general and coding work |
| `moonshotai/Kimi-K3.1-Pro` | long context (1M tokens), strong reasoning |
| `moonshotai/Kimi-K3` | strongest; multimodal |

## 1. Check the price (no payment, no wallet needed)

```bash
npx -y @getalby/cli@0.10.2 fetch "https://lampo.run/v1/chat/completions" \
  -X POST -H '{"Content-Type":"application/json"}' \
  -b '{"model":"deepseek-ai/DeepSeek-V4-Flash-0731","stream":false,"messages":[{"role":"user","content":"hi"}]}' \
  --dry-run
```

## 2. First call: pay for a bundle

Always set `"stream": false`, and always save the response to a file: you paid
for it, so never pipe it through `jq`, `grep` or `head`.

```bash
npx -y @getalby/cli@0.10.2 fetch "https://lampo.run/v1/chat/completions" \
  -X POST -H '{"Content-Type":"application/json"}' \
  -b '{"model":"deepseek-ai/DeepSeek-V4-Flash-0731","stream":false,"messages":[{"role":"user","content":"Summarize this text: ..."}]}' \
  --max-amount 1000 --currency BTC --unit sats --network lightning \
  -o lampo-reply.json
```

Read the saved file first to see its shape. The model's answer is in
`choices[0].message.content` of the response body. The command's JSON output
also has a `payment` object with a reusable `credentials` value: keep it.

## 3. Next calls: reuse the bundle, don't pay again

Pass the saved credential. The same model's bundle keeps working until its
tokens run out.

```bash
npx -y @getalby/cli@0.10.2 fetch "https://lampo.run/v1/chat/completions" \
  -X POST -H '{"Content-Type":"application/json"}' \
  -b '{"model":"deepseek-ai/DeepSeek-V4-Flash-0731","stream":false,"messages":[{"role":"user","content":"Next task ..."}]}' \
  --credentials '<the credentials JSON from step 2>' \
  -o lampo-reply-2.json
```

Bundles are bought per model: keep using the same model with a credential, and
expect a new 402 (a new purchase) when you switch models.

## Errors

- **402 again with a credential:** the bundle is used up, or the credential
  does not fit this model. Stop, tell the user, and ask before paying again.
  Never retry in a loop.
- **429:** the bundle is busy with another request. Wait for `Retry-After`,
  then retry with the same credential.
- **500 on the first request:** usually a wrong model id. Check the table above
  or https://lampo.run/v1/models before paying for anything.
- **Interrupted payment:** follow the `paymentRecovery` steps that `fetch`
  prints, so the same invoice is never paid twice.

## Good practice

- Send only what the task needs: Lampo does not store prompts or responses,
  but they pass through the model provider (Baseten, in the US).
- Don't send secrets, passwords or personal data of other people.
- Terms: https://lampo.run/terms.html. Paying an invoice means accepting them.
  Not available from countries under comprehensive EU sanctions.
