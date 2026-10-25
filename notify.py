"""Telegram notifications for the deal monitor.

  python notify.py chatid          # after pressing Start in the bot: print chat ids that wrote to it
  python notify.py "message text"  # send a message to TELEGRAM_CHAT_ID

Needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (in .env locally, environment variables in the cloud).
"""
import json
import os
import sys
import urllib.parse
import urllib.request

import config  # noqa: F401  (loads .env)


def _api(method, **params):
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or sys.exit("TELEGRAM_BOT_TOKEN is not set")
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/{method}", data=data, timeout=30) as r:
        return json.loads(r.read())


def send(text):
    chat = os.environ.get("TELEGRAM_CHAT_ID") or sys.exit("TELEGRAM_CHAT_ID is not set")
    return _api("sendMessage", chat_id=chat, text=text, disable_web_page_preview="false")


def chat_ids():
    seen = {}
    for u in _api("getUpdates")["result"]:
        chat = (u.get("message") or u.get("my_chat_member") or {}).get("chat")
        if chat:
            seen[chat["id"]] = chat.get("username") or chat.get("first_name") or chat.get("title")
    return seen


if __name__ == "__main__":
    if sys.argv[1:] == ["chatid"]:
        print(chat_ids() or "no messages yet – press Start in the bot first")
    else:
        print(send(" ".join(sys.argv[1:]))["ok"])
