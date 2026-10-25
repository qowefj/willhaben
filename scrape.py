"""Step 1 – data collection via Apify.

  python scrape.py deploy            # create/update + build the private actor from ./apify_actor
  python scrape.py list              # search + all detail pages  -> data/raw_details.json
  python scrape.py images ID [ID..]  # download photos of given ads -> data/photos/<id>/NN.jpg
  python scrape.py images --shortlist  # photos for ids in data/shortlist.json
"""
import io
import json
import re
import sys
import urllib.parse

import apify_api as api
from config import DATA, IMAGES, MAX_IMAGES_PER_AD, OWN_ACTOR_NAME, ROOT, SEARCH_URL

PROXY_DC = {"useApifyProxy": True}
PROXY_RESI = {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"], "apifyProxyCountry": "AT"}


def find_actor():
    for a in api.call("GET", "/acts", params={"my": 1, "limit": 1000})["data"]["items"]:
        if a["name"] == OWN_ACTOR_NAME:
            return a["id"]
    return None


def deploy():
    src = ROOT / "apify_actor"
    files = [
        {"name": p.relative_to(src).as_posix(), "format": "TEXT", "content": p.read_text(encoding="utf8")}
        for p in sorted(src.rglob("*")) if p.is_file()
    ]
    version = {"versionNumber": "0.0", "sourceType": "SOURCE_FILES", "buildTag": "latest", "sourceFiles": files}
    actor_id = find_actor()
    if actor_id:
        api.call("PUT", f"/acts/{actor_id}/versions/0.0", body=version)
    else:
        actor_id = api.call("POST", "/acts", body={
            "name": OWN_ACTOR_NAME, "isPublic": False, "versions": [version],
            "defaultRunOptions": {"build": "latest", "memoryMbytes": 1024, "timeoutSecs": 3600},
        })["data"]["id"]
    build = api.call("POST", f"/acts/{actor_id}/builds", params={"version": "0.0", "useCache": 1})["data"]
    print(f"actor {actor_id}: building {build['id']}")
    _wait_build(build["id"])
    return actor_id


def _wait_build(build_id):
    import time
    while True:
        b = api.call("GET", f"/actor-builds/{build_id}")["data"]
        print("  build", b["status"], flush=True)
        if b["status"] in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            if b["status"] != "SUCCEEDED":
                print(api.call("GET", f"/logs/{build_id}", raw=True).decode("utf8", "replace")[-3000:])
                sys.exit("build failed")
            return b
        time.sleep(8)


def find_task(actor_id, label):
    """Runs have no title in Apify, but a task's name shows in the run history -> one named task per search."""
    name = "wh-" + re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:59]
    for t in api.call("GET", "/actor-tasks", params={"limit": 1000})["data"]["items"]:
        if t["name"] == name:
            return t["id"]
    return api.call("POST", "/actor-tasks", body={"actId": actor_id, "name": name})["data"]["id"]


def run_actor(run_input, label, memory=1024):
    actor_id = find_actor() or sys.exit("actor not deployed – run `python scrape.py deploy`")
    task_id = find_task(actor_id, label)
    run = api.call("POST", f"/actor-tasks/{task_id}/runs", body=run_input, params={"memory": memory})["data"]
    print(f"{label}: run {run['id']}")
    run = api.wait_for_run(run["id"], label=label)
    if run["status"] != "SUCCEEDED":
        print(api.run_log(run["id"])[-3000:])
        sys.exit(f"{label} run {run['status']}")
    return run


def cmd_list(proxy=PROXY_DC):
    run = run_actor({"mode": "list", "searchUrls": [SEARCH_URL], "proxy": proxy}, "list")
    items = api.dataset_items(run["defaultDatasetId"])
    log = api.run_log(run["id"])
    print("\n".join(l for l in log.splitlines() if "Search page" in l or "FAILED" in l)[-3000:])
    (DATA / "raw_details.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf8")
    print(f"saved {len(items)} adverts -> data/raw_details.json")


def cmd_retry(proxy=PROXY_RESI):
    """Re-fetch detail pages that failed (403) in the last list run and merge them in."""
    log = api.run_log(api.call("GET", "/actor-runs", params={"desc": 1, "limit": 1})["data"]["items"][0]["id"])
    path = DATA / "raw_details.json"
    have = json.loads(path.read_text(encoding="utf8"))
    done = {x["id"] for x in have}
    failed = (DATA / "failed_urls.txt").read_text().split() if (DATA / "failed_urls.txt").exists() else []
    failed += re.findall(r"FAILED (https://\S+?/):", log)
    failed = [u for u in failed if re.search(r"-(\d+)/$", u)]
    urls = sorted({u for u in failed if re.search(r"-(\d+)/$", u).group(1) not in done})
    print(f"retrying {len(urls)} detail pages")
    if not urls:
        return
    run = run_actor({"mode": "list", "detailUrls": urls, "proxy": proxy}, "retry")
    new = [x for x in api.dataset_items(run["defaultDatasetId"]) if x["id"] not in done]
    path.write_text(json.dumps(have + new, ensure_ascii=False), encoding="utf8")
    still = [u for u in urls if re.search(r"-(\d+)/$", u).group(1) not in {x["id"] for x in new}]
    (DATA / "failed_urls.txt").write_text("\n".join(still))
    print(f"+{len(new)} adverts (total {len(have) + len(new)}), still failing: {len(still)}")


def save_jpeg(blob, path):
    """Willhaben serves WebP; store real JPEGs so every vision API / viewer accepts them."""
    from PIL import Image   # lazy: only photo downloads need Pillow (broken in some cloud images)
    try:
        img = Image.open(io.BytesIO(blob))
    except Exception:
        print("  unreadable image, skipped:", path)
        return
    img.convert("RGB").save(path, "JPEG", quality=88)


def cmd_images(ids, proxy=PROXY_DC):
    raw = {x["id"]: x for x in json.loads((DATA / "raw_details.json").read_text(encoding="utf8"))}
    jobs = []
    for ad_id in ids:
        imgs = (raw[ad_id]["advertDetails"].get("advertImageList") or {}).get("advertImage") or []
        for i, img in enumerate(imgs[:MAX_IMAGES_PER_AD]):
            if (IMAGES / ad_id / f"{i:02d}.jpg").exists():
                continue
            jobs.append({"key": f"img-{ad_id}-{i:02d}", "url": img.get("referenceImageUrl") or img.get("mainImageUrl")})
    print(f"{len(jobs)} images to fetch for {len(ids)} adverts")
    if not jobs:
        return
    run = run_actor({"mode": "images", "images": jobs, "proxy": proxy}, "images")
    store = run["defaultKeyValueStoreId"]
    got = 0
    for job in jobs:
        try:
            blob = api.call("GET", f"/key-value-stores/{store}/records/{urllib.parse.quote(job['key'])}", raw=True)
        except RuntimeError as e:
            print("  missing", job["key"], str(e)[:80])
            continue
        _, ad_id, idx = job["key"].split("-")
        (IMAGES / ad_id).mkdir(exist_ok=True)
        save_jpeg(blob, IMAGES / ad_id / f"{idx}.jpg")
        got += 1
    print(f"downloaded {got}/{len(jobs)} images -> data/photos/")


if __name__ == "__main__":
    args = sys.argv[1:] or ["list"]
    resi = "--resi" in args
    args = [a for a in args if a != "--resi"]
    proxy = PROXY_RESI if resi else PROXY_DC
    if args[0] == "deploy":
        deploy()
    elif args[0] == "retry":
        cmd_retry(PROXY_DC if "--dc" in args else PROXY_RESI)
    elif args[0] == "list":
        cmd_list(proxy)
    elif args[0] == "images":
        ids = json.loads((DATA / "shortlist.json").read_text())["ids"] if "--shortlist" in args else args[1:]
        cmd_images(ids, proxy)
