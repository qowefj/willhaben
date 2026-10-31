"""Client for host_service/ (Willhaben fetcher + state store on the docker host). Stdlib only."""
import json
import urllib.error
import urllib.parse
import urllib.request

from config import WH_HOST_TOKEN, WH_HOST_URL


def call(method, path, body=None, params=None, raw=False, timeout=300):
    if not (WH_HOST_URL and WH_HOST_TOKEN):
        raise RuntimeError("WH_HOST_URL / WH_HOST_TOKEN not set")
    url = WH_HOST_URL.rstrip("/") + path + (f"?{urllib.parse.urlencode(params)}" if params else "")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {WH_HOST_TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"wh-host {method} {path} -> {e.code}: {e.read().decode('utf8', 'replace')[:500]}") from None
    if raw:
        return payload
    return json.loads(payload) if payload else None


def crawl(run_input, label=""):
    """Same input as the Apify actor's "list" mode; returns its dataset items. Prints the crawl log."""
    res = call("POST", "/list", body=run_input)
    for line in res["log"].splitlines():
        print(f"  [{label}] {line}" if label else f"  {line}", flush=True)
    return res["items"]


def image(url):
    return call("GET", "/image", params={"url": url}, raw=True, timeout=60)


def get_state(name):
    """Stored JSON record, or None if it was never written."""
    try:
        return call("GET", f"/state/{name}")
    except RuntimeError as e:
        if "-> 404" in str(e):
            return None
        raise


def put_state(name, value):
    call("PUT", f"/state/{name}", body=value)
