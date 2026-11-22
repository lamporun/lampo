#!/usr/bin/env python3
"""Prints, then clears, what the operator did on Telegram since the last call:
button presses ({"type": "decision"}) and messages ({"type": "text"}), one JSON
object per line. Written by lampo-tgbot.service, which only listens to the
operator's own chat. Run as root over ssh by the assistant on the PC.
"""
import fcntl
import os

QUEUE = "/var/lib/lampo/tg-queue.jsonl"

if os.path.exists(QUEUE):
    with open(QUEUE, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        data = f.read()
        f.seek(0)
        f.truncate()
    print(data, end="")
