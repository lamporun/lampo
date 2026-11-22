#!/usr/bin/env python3
"""Lampo's Telegram bot, always on (lampo-tgbot.service).

Records the operator's button presses ("Pubblica" / "Scarta") and messages in
/var/lib/lampo/tg-queue.jsonl for the assistant on the PC, and answers at once
so the buttons feel instant. Only the operator's own chat counts.
"""
import fcntl
import json
import re
import time
import urllib.parse
import urllib.request

ENV = "/etc/lampo/alert.env"
QUEUE = "/var/lib/lampo/tg-queue.jsonl"
OFFSET = "/var/lib/lampo/tg-bot-offset"


def read_env(path):
    out = {}
    with open(path) as f:
        for line in f:
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.strip().split("=", 1)
                out[k] = v.strip().strip('"').strip("'")
    return out


E = read_env(ENV)
API = f"https://api.telegram.org/bot{E['TELEGRAM_TOKEN']}/"
CHAT = str(E["TELEGRAM_CHAT_ID"])


def call(method, **params):
    data = urllib.parse.urlencode({k: json.dumps(v) if isinstance(v, (dict, list)) else v
                                   for k, v in params.items()}).encode()
    with urllib.request.urlopen(API + method, data=data, timeout=70) as r:
        return json.load(r)


def quiet(method, **params):
    try:
        call(method, **params)
    except Exception:
        pass


def enqueue(obj):
    with open(QUEUE, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def handle(u):
    cq = u.get("callback_query")
    if cq:
        if str(cq["from"]["id"]) != CHAT:
            return
        act, _, n = (cq.get("data") or "").partition(":")
        if act not in ("ok", "no") or not n.isdigit():
            return
        enqueue({"type": "decision", "id": int(n), "ok": act == "ok", "date": int(time.time())})
        label = ("✅ Approvata: la pubblico entro pochi minuti, se il PC è acceso. Ti arriva la conferma."
                 if act == "ok" else "❌ Scartata.")
        quiet("answerCallbackQuery", callback_query_id=cq["id"], text=label[:190])
        m = cq.get("message") or {}
        if m:
            quiet("editMessageText", chat_id=m["chat"]["id"], message_id=m["message_id"],
                  text=(m.get("text") or "") + "\n\n" + label)
        return
    m = u.get("message") or {}
    if str(m.get("chat", {}).get("id")) != CHAT or not m.get("text"):
        return
    text = m["text"].strip()
    if text == "/start":
        quiet("sendMessage", chat_id=CHAT, text="Ciao! Qui ti arrivano le bozze di Lampo: premi ✅ Pubblica o ❌ Scarta.")
        return
    enqueue({"type": "text", "date": m["date"], "text": text})
    if re.fullmatch(r"(ok|no)\s+[\d ,]+", text, re.I):
        reply = "Ricevuto: lo eseguo entro pochi minuti, se il PC è acceso."
    else:
        reply = "Ricevuto: lo leggo alla prossima corsa del mattino (9:30)."
    quiet("sendMessage", chat_id=CHAT, text=reply)


def main():
    try:
        offset = int(open(OFFSET).read().strip() or 0)
    except (OSError, ValueError):
        offset = 0
    while True:
        try:
            res = call("getUpdates", offset=offset, timeout=50,
                       allowed_updates=["message", "callback_query"])
        except Exception:
            time.sleep(10)
            continue
        for u in res.get("result", []):
            offset = max(offset, u["update_id"] + 1)
            try:
                handle(u)
            except Exception:
                pass
        with open(OFFSET, "w") as f:
            f.write(str(offset))


if __name__ == "__main__":
    main()
