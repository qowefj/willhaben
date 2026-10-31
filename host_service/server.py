"""Willhaben fetcher + tiny state store, run on the docker host (replaces the Apify actor and key-value store).

Willhaben does not block the docker host's IP, so plain HTTPS requests are enough (no proxy, no browser).
Stdlib only. Every endpoint except /healthz needs  Authorization: Bearer $TOKEN.

  GET  /healthz
  POST /list           same input as apify_actor "list" mode -> {"items": [...], "log": "..."}
                       items: {summary:true,id,title,price,seo} (pushSummaries) and {id,url,advertDetails}
  GET  /image?url=...  one Willhaben image (cache.willhaben.at only), bytes as served
  GET  /state/<name>   JSON record (404 if never written)
  PUT  /state/<name>   replace it
"""
import hmac
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TOKEN = os.environ["TOKEN"]
STATE_DIR = Path(os.environ.get("STATE_DIR", "/data"))
PORT = int(os.environ.get("PORT", "8080"))
CONCURRENCY = int(os.environ.get("CONCURRENCY", "4"))

SITE = "https://www.willhaben.at/"
IMAGE_HOST = "https://cache.willhaben.at/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
STATE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
state_lock = threading.Lock()


def fetch(url, accept="text/html", tries=4):
    for attempt in range(tries):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept,
                                                   "Accept-Language": "de-AT,de;q=0.9,en;q=0.5"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read(), r.headers.get("Content-Type", "")
        except (urllib.error.URLError, TimeoutError) as e:
            if isinstance(e, urllib.error.HTTPError) and e.code == 404 or attempt == tries - 1:
                raise
            time.sleep(2 * 2 ** attempt)


def page_props(url):
    html = fetch(url)[0].decode("utf8", "replace")
    m = NEXT_DATA.search(html)
    if not m:
        title = re.search(r"<title>(.*?)</title>", html, re.S)
        raise RuntimeError(f'No __NEXT_DATA__ (blocked?) title="{title.group(1).strip() if title else ""}"')
    return json.loads(m.group(1))["props"]["pageProps"]


def crawl(inp):
    """Mirror of apify_actor/main.js mode "list": search pages (paginated) + detail pages of unknown ads."""
    search_urls, detail_urls = inp.get("searchUrls", []), list(inp.get("detailUrls", []))
    for u in search_urls + detail_urls:
        if not u.startswith(SITE):
            raise ValueError(f"not a Willhaben URL: {u}")
    max_pages, max_details = inp.get("maxPages", 20), inp.get("maxDetails", 2000)
    push_summaries, skip = inp.get("pushSummaries", False), {str(i) for i in inp.get("skipIds", [])}
    items, log = [], []

    for url in search_urls:
        page = 1
        while True:
            u = url if page == 1 else _with_page(url, page)
            try:
                sr = page_props(u).get("searchResult") or {}
            except Exception as e:
                log.append(f"FAILED {u}: {e}")
                break
            ads = (sr.get("advertSummaryList") or {}).get("advertSummary") or []
            log.append(f"Search page {page}: {len(ads)} ads, rowsFound={sr.get('rowsFound')}")
            for ad in ads:
                attrs = {a["name"]: (a.get("values") or [None])[0] for a in ad["attributes"]["attribute"]}
                seo = attrs.get("SEO_URL")
                if push_summaries:
                    items.append({"summary": True, "id": str(ad["id"]), "title": ad.get("description"),
                                  "price": attrs.get("PRICE"), "seo": seo})
                if seo and len(detail_urls) < max_details and str(ad["id"]) not in skip:
                    detail_urls.append(f"{SITE}iad/{seo}")
            rows = sr.get("rowsRequested") or len(ads)
            if not (ads and page * rows < (sr.get("rowsFound") or 0) and page < max_pages):
                break
            page += 1

    def detail(url):
        try:
            ad = page_props(url).get("advertDetails")
            if not ad:
                raise RuntimeError("No advertDetails")
            return {"id": str(ad["id"]), "url": url, "advertDetails": ad}
        except Exception as e:
            log.append(f"FAILED {url}: {e}")

    with ThreadPoolExecutor(CONCURRENCY) as pool:
        items += [d for d in pool.map(detail, dict.fromkeys(detail_urls)) if d]
    return {"items": items, "log": "\n".join(log)}


def _with_page(url, page):
    parts = urllib.parse.urlsplit(url)
    q = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query) if k != "page"] + [("page", str(page))]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(q)))


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body=b"", ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        got = self.headers.get("Authorization", "")
        if hmac.compare_digest(got.encode(), f"Bearer {TOKEN}".encode()):
            return True
        self._send(401, {"error": "unauthorized"})
        return False

    def _body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"null")

    def _state_path(self, path):
        name = path.removeprefix("/state/")
        return STATE_DIR / f"{name}.json" if STATE_NAME.match(name) else None

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        if url.path == "/healthz":
            return self._send(200, {"ok": True})
        if not self._authorized():
            return
        if url.path == "/image":
            src = urllib.parse.parse_qs(url.query).get("url", [""])[0]
            if not src.startswith(IMAGE_HOST):
                return self._send(400, {"error": "only cache.willhaben.at images"})
            try:
                blob, ctype = fetch(src, accept="image/jpeg,image/png;q=0.9,image/*;q=0.5")
            except Exception as e:
                return self._send(502, {"error": str(e)})
            return self._send(200, blob, ctype or "application/octet-stream")
        if url.path.startswith("/state/"):
            p = self._state_path(url.path)
            if not p:
                return self._send(400, {"error": "bad state name"})
            with state_lock:
                return self._send(200, p.read_bytes()) if p.exists() else self._send(404, {"error": "no record"})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._authorized():
            return
        if self.path != "/list":
            return self._send(404, {"error": "not found"})
        try:
            self._send(200, crawl(self._body() or {}))
        except ValueError as e:
            self._send(400, {"error": str(e)})

    def do_PUT(self):
        if not self._authorized():
            return
        p = self._state_path(self.path)
        if not p:
            return self._send(400, {"error": "bad state name"})
        data = json.dumps(self._body(), ensure_ascii=False)
        with state_lock:
            tmp = p.with_suffix(".tmp")
            tmp.write_text(data, encoding="utf8")
            tmp.replace(p)
        self._send(204)

    def log_message(self, fmt, *args):   # no query strings in the log (image URLs are fine, but keep it short)
        print(f"{self.address_string()} {self.command} {self.path.split('?')[0]} {args[1] if len(args) > 1 else ''}",
              flush=True)


if __name__ == "__main__":
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"listening on :{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
