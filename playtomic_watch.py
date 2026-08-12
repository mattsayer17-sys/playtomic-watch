#!/usr/bin/env python3
"""
playtomic_watch.py -- READ-ONLY availability watcher for Playtomic events at
PADELHUB KT19 Epsom. Sends a Telegram alert the instant a place frees up on a
FULL event (free-places transition from 0 -> positive), so you can grab it
yourself before anyone else.

WHY THE WEBSITE INSTEAD OF api.playtomic.io
-------------------------------------------
The documented app endpoints (api.playtomic.io: v1/tenants, v2/tournaments,
v1/lessons, v3/auth/login) sit behind a CloudFront WAF that returns
403 "Request blocked" to every non-app client reproducible from a normal
machine -- plain requests, curl, browser-TLS impersonation and even a real
browser -- regardless of egress IP or the X-Requested-With header. Verified
2026-08-12 from two different egress IPs.

The public website playtomic.com is NOT blocked. It is server-side rendered:
Playtomic's own backend fetches the same data from the API and bakes it into
the page HTML. So this tool reads the server-rendered public pages. That means:
no login, no account, no credentials -- fully anonymous -- and strictly
read-only. Every field below was confirmed against live pages, not guessed.

VERIFIED FACTS (2026-08-12)
  tenant_id  PADELHUB KT19 Epsom = 04dd6b80-e488-4af3-b740-78704af847ef
  /clubs/<tenant_id>                       302 -> /clubs/<slug>
  /clubs/<slug>/competitions/tournaments   lists tournament ids
  /activities/tournaments/<id>             event detail page
  free places  <progress value="TAKEN" max="CAPACITY" aria-label="T/C spots">
               -> free = CAPACITY - TAKEN        (event is full when free == 0)
  level        rendered as "[Restricted . ] MIN - MAX"  e.g. "0 - 7", "3.75 - 7"

READ ONLY GUARANTEE
  This program only performs HTTP GETs against public pages and one Telegram
  sendMessage POST. It contains no code that books, joins, reserves, pays,
  cancels, or removes any player from anything -- by design.
"""

import argparse
import html as htmllib
import json
import os
import re
import sys
import time

import requests

BASE = "https://playtomic.com"
DEFAULT_TENANT_ID = "04dd6b80-e488-4af3-b740-78704af847ef"  # PADELHUB KT19 Epsom
DEFAULT_STATE = "playtomic_state.json"
TIMEOUT = 30
GUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

# The X-Requested-With value the app sends, per the original brief. It is not
# required by the website, but is harmless and included as requested.
SESSION_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept-Language": "en-GB,en;q=0.9",
    "X-Requested-With": "com.playtomic.app 6.13.0",
}


# --------------------------------------------------------------------------- #
# HTTP with exponential backoff on 429
# --------------------------------------------------------------------------- #
def make_session():
    s = requests.Session()
    s.headers.update(SESSION_HEADERS)
    return s


def fetch(session, url, max_retries=5):
    """GET a URL, backing off exponentially on HTTP 429. Returns the response."""
    delay = 2.0
    for attempt in range(max_retries + 1):
        r = session.get(url, timeout=TIMEOUT, allow_redirects=True)
        if r.status_code == 429:
            if attempt == max_retries:
                raise RuntimeError(f"429 rate-limited after {max_retries} retries: {url}")
            wait = r.headers.get("Retry-After")
            wait = float(wait) if (wait and wait.isdigit()) else delay
            print(f"  [429] backing off {wait:.0f}s ({url[-48:]})", file=sys.stderr)
            time.sleep(wait)
            delay = min(delay * 2, 60)
            continue
        if r.status_code == 403 and "Request blocked" in r.text:
            raise RuntimeError(
                "403 Request blocked by CloudFront WAF -- this host is not the "
                "website. Use playtomic.com pages, not api.playtomic.io.")
        r.raise_for_status()
        return r
    raise RuntimeError(f"exhausted retries: {url}")


# --------------------------------------------------------------------------- #
# Parsing the server-rendered pages
# --------------------------------------------------------------------------- #
def resolve_slug(session, tenant):
    """Turn a tenant_id GUID into the club slug via the /clubs/<id> redirect.
    If `tenant` is already a slug, return it unchanged."""
    if not GUID_RE.match(tenant):
        return tenant
    r = fetch(session, f"{BASE}/clubs/{tenant}")
    slug = r.url.rstrip("/").split("/clubs/", 1)[-1].split("/")[0]
    if not slug or slug == tenant:
        raise RuntimeError(f"could not resolve slug for tenant_id {tenant}")
    return slug


def list_tournament_ids(session, slug):
    """Return tournament ids for a club, in page order, de-duplicated."""
    html = fetch(session, f"{BASE}/clubs/{slug}/competitions/tournaments").text
    seen, ids = set(), []
    for m in re.finditer(r"/activities/tournaments/([0-9a-f-]{36})", html):
        tid = m.group(1)
        if tid not in seen:
            seen.add(tid)
            ids.append(tid)
    return ids


def _labelled_value(html, label):
    """Extract the text of the <p> value rendered after a "<p>Label</p>" tag."""
    m = re.search(r">%s<\s*/\s*p>.*?<p[^>]*>(.*?)</p>" % re.escape(label), html, re.S)
    if not m:
        return None
    return htmllib.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()


def parse_event(session, tid):
    """Fetch and parse one tournament detail page into a dict."""
    html = fetch(session, f"{BASE}/activities/tournaments/{tid}").text

    title = None
    mt = re.search(r"<title>(.*?)</title>", html, re.S)
    if mt:
        title = htmllib.unescape(mt.group(1)).split("|")[0].strip()

    taken = capacity = free = None
    mp = re.search(r'<progress[^>]*\bvalue="(\d+)"[^>]*\bmax="(\d+)"', html)
    if mp:
        taken, capacity = int(mp.group(1)), int(mp.group(2))
        free = capacity - taken

    level_raw = _labelled_value(html, "Level")
    lmin = lmax = None
    if level_raw:
        ml = re.search(r"(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)", level_raw)
        if ml:
            lmin, lmax = float(ml.group(1)), float(ml.group(2))

    return {
        "id": tid,
        "name": title,
        "date": _labelled_value(html, "Date"),
        "taken": taken,
        "capacity": capacity,
        "free": free,
        "full": (free == 0) if free is not None else None,
        "level_raw": level_raw,
        "level_min": lmin,
        "level_max": lmax,
        "url": f"{BASE}/activities/tournaments/{tid}",
    }


def get_all_events(session, tenant, polite=0.4):
    slug = resolve_slug(session, tenant)
    events = []
    for tid in list_tournament_ids(session, slug):
        events.append(parse_event(session, tid))
        if polite:
            time.sleep(polite)
    return slug, events


# --------------------------------------------------------------------------- #
# Filtering
# --------------------------------------------------------------------------- #
def name_matches(event, matches):
    if not matches:
        return True
    name = (event.get("name") or "").lower()
    return any(m.lower() in name for m in matches)


def level_qualifies(event, min_level, max_level):
    """True if the event's [level_min, level_max] band overlaps the user's
    acceptable [min_level, max_level] window. Unknown level -> keep (don't drop).
    """
    if min_level is None and max_level is None:
        return True
    emin, emax = event.get("level_min"), event.get("level_max")
    if emin is None or emax is None:
        return True
    lo = min_level if min_level is not None else float("-inf")
    hi = max_level if max_level is not None else float("inf")
    return emin <= hi and lo <= emax


# --------------------------------------------------------------------------- #
# State + Telegram
# --------------------------------------------------------------------------- #
def load_state(path):
    try:
        # utf-8-sig tolerates a BOM (e.g. if the file was edited in Notepad).
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, path)


def send_telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("  [telegram] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set -- "
              "skipping push.", file=sys.stderr)
        return False
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": False},
        timeout=TIMEOUT,
    )
    ok = r.ok and r.json().get("ok", False)
    if not ok:
        print(f"  [telegram] send failed: {r.status_code} {r.text[:180]}", file=sys.stderr)
    return ok


def alert_text(ev):
    lvl = ev.get("level_raw") or "?"
    return (f"\U0001F7E2 Spot opened at PADELHUB KT19 Epsom!\n"
            f"{ev.get('name')}\n"
            f"{ev.get('date')}\n"
            f"Level: {lvl}\n"
            f"Free: {ev.get('free')}/{ev.get('capacity')}\n"
            f"Grab it: {ev.get('url')}")


# --------------------------------------------------------------------------- #
# Subcommands
# --------------------------------------------------------------------------- #
def cmd_discover(args):
    s = make_session()
    slug, events = get_all_events(s, args.tenant_id)
    print(f"tenant_id : {args.tenant_id}")
    print(f"slug      : {slug}")
    print(f"events    : {len(events)}\n")
    for e in events:
        flag = "FULL" if e["full"] else f"{e['free']} free" if e["free"] is not None else "?"
        print(f"  {e['name']:<28} | {str(e['date']):<28} | "
              f"{str(e['taken'])}/{str(e['capacity']):<3} ({flag:>7}) | "
              f"level {e['level_raw']} | {e['id']}")


def cmd_dump(args):
    s = make_session()
    _, events = get_all_events(s, args.tenant_id)
    print(json.dumps(events, indent=2, ensure_ascii=False))


def cmd_watch(args):
    s = make_session()

    # Resolve the set of events to watch once, up front.
    slug, all_events = get_all_events(s, args.tenant_id)
    watched = [e for e in all_events
               if name_matches(e, args.match)
               and level_qualifies(e, args.min_level, args.max_level)]
    if not watched:
        print("No events match your --match / level filter. Nothing to watch.")
        print("Tip: run `discover` to see available event names and levels.")
        return

    print(f"Watching {len(watched)} event(s) at {slug} "
          f"every {args.interval}s (state: {args.state}):")
    for e in watched:
        print(f"  - {e['name']} | {e['date']} | level {e['level_raw']} "
              f"| currently {e['free']}/{e['capacity']} free")
    print()

    watch_ids = [e["id"] for e in watched]
    meta = {e["id"]: e for e in watched}

    while True:
        state = load_state(args.state)
        for tid in watch_ids:
            try:
                ev = parse_event(s, tid)
            except Exception as exc:  # keep the loop alive on transient errors
                print(f"  [warn] {tid}: {exc}", file=sys.stderr)
                continue
            meta[tid] = ev
            free = ev["free"]
            if free is None:
                continue
            prev = state.get(tid)
            stamp = time.strftime("%H:%M:%S")
            print(f"  {stamp} {ev['name'][:30]:<30} free={free}/{ev['capacity']} "
                  f"(prev={prev})")
            # Alert ONLY on a 0 -> positive transition.
            if prev == 0 and free > 0:
                if send_telegram(alert_text(ev)):
                    print(f"  >>> ALERT sent: {ev['name']} freed up ({free} spot/s)")
            state[tid] = free
            if args.polite:
                time.sleep(args.polite)
        save_state(args.state, state)

        if args.once:
            break
        time.sleep(args.interval)


# --------------------------------------------------------------------------- #
def build_parser():
    p = argparse.ArgumentParser(
        description="READ-ONLY Playtomic free-place watcher (PADELHUB KT19 Epsom).")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_tenant(sp):
        sp.add_argument("--tenant-id", default=DEFAULT_TENANT_ID,
                        help="Tenant GUID or club slug (default: PADELHUB KT19 Epsom).")

    sp = sub.add_parser("discover", help="List the club's events with levels and free places.")
    add_tenant(sp)
    sp.set_defaults(func=cmd_discover)

    sp = sub.add_parser("dump", help="Dump all parsed events as JSON.")
    add_tenant(sp)
    sp.set_defaults(func=cmd_dump)

    sp = sub.add_parser("watch", help="Poll matched events and alert on 0->positive.")
    add_tenant(sp)
    sp.add_argument("--match", action="append", default=[],
                    help="Event-name substring to watch (repeatable). "
                         "Default: all events.")
    sp.add_argument("--min-level", type=float, default=None,
                    help="Lower bound of your acceptable level window.")
    sp.add_argument("--max-level", type=float, default=None,
                    help="Upper bound of your acceptable level window.")
    sp.add_argument("--interval", type=int, default=60,
                    help="Seconds between polling cycles (default: 60).")
    sp.add_argument("--polite", type=float, default=0.5,
                    help="Seconds to pause between per-event requests (default: 0.5).")
    sp.add_argument("--state", default=DEFAULT_STATE,
                    help=f"JSON state file (default: {DEFAULT_STATE}).")
    sp.add_argument("--once", action="store_true",
                    help="Run a single cycle and exit (for testing / cron).")
    sp.set_defaults(func=cmd_watch)
    return p


def main():
    args = build_parser().parse_args()
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
