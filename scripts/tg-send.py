#!/usr/bin/env python3
"""Sends the text on stdin to the operator's Telegram chat (split if long)."""
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


def main():
    env = read_env(ENV)
    text = sys.stdin.read().strip()
    if not text:
        return
    url = f"https://api.telegram.org/bot{env['TELEGRAM_TOKEN']}/sendMessage"
    for i in range(0, len(text), 3900):
        data = urllib.parse.urlencode({"chat_id": env["TELEGRAM_CHAT_ID"], "text": text[i:i + 3900],
                                       "disable_web_page_preview": "true"}).encode()
        urllib.request.urlopen(url, data=data, timeout=30).read()


if __name__ == "__main__":
    main()
