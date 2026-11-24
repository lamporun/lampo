#!/usr/bin/env python3
"""@LampoGuard_bot, first step of hosted nwc-guard: the beta waitlist.

  /start   what nwc-guard is and what the hosted version will do
  /beta    join the hosted beta waitlist (the bot will write here when a spot opens)
  /leave   leave the waitlist
  /waitlist  (operator only) how many are waiting, and the latest ones

Stores only what a waitlist needs (Telegram chat id, username, first name, date)
in /var/lib/lampo/guard/waitlist.json. Message texts are not stored or logged.
Settings: /etc/lampo/guardbot.env (GUARDBOT_TOKEN, GUARDBOT_OPERATOR_CHAT).
"""
import json
import os
import subprocess
import time
import urllib.request

TOKEN = os.environ["GUARDBOT_TOKEN"]
OPERATOR = str(os.environ.get("GUARDBOT_OPERATOR_CHAT", ""))
STORE = os.environ.get("GUARDBOT_WAITLIST", "/var/lib/lampo/guard/waitlist.json")
ALERT = "/opt/lampo/bin/alert.sh"
API = f"https://api.telegram.org/bot{TOKEN}"

WELCOME = (
    "🛡 nwc-guard: a spending firewall between your AI agent and your Lightning wallet.\n\n"
    "Your agent gets an NWC connection that points to the guard. Every payment is checked: who gets paid, "
    "how much, how often. Anything new waits for your ✅ here on Telegram. /stop pauses everything.\n\n"
    "The open-source version runs on your own machine: https://github.com/lamporun/nwc-guard\n\n"
    "The hosted version is coming: nothing to install or keep online, and approvals right in this chat. "
    "Send /beta to join the waitlist."
)
JOINED = (
    "✅ You're on the hosted beta waitlist (#{n}). We'll write to you here when your spot opens.\n\n"
    "Meanwhile you can run it yourself: https://github.com/lamporun/nwc-guard\n"
    "Send /leave to leave the list."
)
HELP = "Send /beta to join the hosted beta waitlist, or /start to read what nwc-guard does."


def call(method, **params):
    req = urllib.request.Request(f"{API}/{method}", data=json.dumps(params).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.load(r)


def say(chat, text):
    try:
        call("sendMessage", chat_id=chat, text=text, disable_web_page_preview=True)
    except Exception as e:  # never crash on one failed reply
        print("sendMessage failed:", type(e).__name__)


def load():
    try:
        with open(STORE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"version": 1, "people": []}


def save(data):
    os.makedirs(os.path.dirname(STORE), mode=0o750, exist_ok=True)
    tmp = STORE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.chmod(tmp, 0o640)
    os.replace(tmp, STORE)


def alert(text):
    if os.path.exists(ALERT):
        subprocess.run([ALERT, text], check=False, timeout=30)


def handle(msg):
    chat = msg.get("chat", {})
    if chat.get("type") != "private":
        return
    cid = str(chat["id"])
    text = (msg.get("text") or "").strip()
    command = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
    data = load()
    people = data["people"]
    payload = text.split()[1].lower() if len(text.split()) > 1 else ""
    if command == "/start" and payload == "beta":
        command = "/beta"  # t.me/LampoGuard_bot?start=beta joins in one tap
    if command == "/start":
        say(cid, WELCOME)
    elif command == "/beta":
        for i, p in enumerate(people):
            if p["chat_id"] == cid:
                say(cid, JOINED.format(n=i + 1))
                return
        user = msg.get("from", {})
        people.append({"chat_id": cid, "username": (user.get("username") or "")[:64],
                       "first_name": (user.get("first_name") or "")[:64], "joined": int(time.time())})
        save(data)
        say(cid, JOINED.format(n=len(people)))
        who = f"@{user['username']}" if user.get("username") else (user.get("first_name") or "qualcuno")
        alert(f"Nuovo iscritto alla beta di nwc-guard ospitato: {who} (#{len(people)}).")
    elif command == "/leave":
        data["people"] = [p for p in people if p["chat_id"] != cid]
        save(data)
        say(cid, "You've left the waitlist. Send /beta to join again.")
    elif command == "/waitlist" and cid == OPERATOR:
        last = ", ".join((f"@{p['username']}" if p["username"] else p["first_name"]) for p in people[-10:])
        say(cid, f"Waitlist: {len(people)}. Latest: {last or '(none)'}")
    else:
        say(cid, HELP)


def main():
    offset = 0
    while True:
        try:
            res = call("getUpdates", offset=offset, timeout=25, allowed_updates=["message"])
            for update in res.get("result", []):
                offset = update["update_id"] + 1
                if "message" in update:
                    handle(update["message"])
        except Exception as e:
            print("poll error:", type(e).__name__)
            time.sleep(5)


if __name__ == "__main__":
    main()
