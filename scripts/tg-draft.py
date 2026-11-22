#!/usr/bin/env python3
"""Sends one draft (text on stdin) to the operator with ✅ Pubblica / ❌ Scarta
buttons. Usage: tg-draft.py DRAFT_NUMBER"""
import json
import sys
import urllib.parse
import urllib.request

ENV = "/etc/lampo/alert.env"


def read_env(path):
    out = {}
    with open(path) as f:
        for line in f:
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.strip().split("=", 1)
                out[k] = v.strip().strip('"').strip("'")
    return out


n = int(sys.argv[1])
env = read_env(ENV)
keyboard = {"inline_keyboard": [[{"text": "✅ Pubblica", "callback_data": f"ok:{n}"},
                                 {"text": "❌ Scarta", "callback_data": f"no:{n}"}]]}
data = urllib.parse.urlencode({"chat_id": env["TELEGRAM_CHAT_ID"], "text": sys.stdin.read().strip()[:4000],
                               "reply_markup": json.dumps(keyboard), "disable_web_page_preview": "true"}).encode()
urllib.request.urlopen(f"https://api.telegram.org/bot{env['TELEGRAM_TOKEN']}/sendMessage", data=data, timeout=30).read()
