"""Minimal Apify REST client (stdlib only)."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from config import APIFY_TOKEN

BASE = "https://api.apify.com/v2"


def call(method, path, body=None, params=None, raw=False, timeout=120):
    q = {"token": APIFY_TOKEN, **(params or {})}
    url = f"{BASE}{path}?{urllib.parse.urlencode(q)}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Apify {method} {path} -> {e.code}: {e.read().decode('utf8', 'replace')[:500]}") from None
    if raw:
        return payload
    return json.loads(payload) if payload else None


def wait_for_run(run_id, poll=10, label="run"):
    while True:
        run = call("GET", f"/actor-runs/{run_id}")["data"]
        status = run["status"]
        print(f"  [{label}] {status}  usage=${run.get('usageTotalUsd') or 0:.4f}", flush=True)
        if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            return run
        time.sleep(poll)


def dataset_items(dataset_id):
    items, offset = [], 0
    while True:
        chunk = call("GET", f"/datasets/{dataset_id}/items", params={"offset": offset, "limit": 500, "clean": 1}, timeout=300)
        items += chunk
        if len(chunk) < 500:
            return items
        offset += 500


def run_log(run_id):
    return call("GET", f"/actor-runs/{run_id}/log", raw=True).decode("utf8", "replace")
