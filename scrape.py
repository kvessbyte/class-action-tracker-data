#!/usr/bin/env python3
"""Daily scraper for Canadian class action trackers.

Fetches the public listing pages, extracts case names / statuses / deadlines,
merges them into data.json, and stamps an updated date. The Canada Class Action
Tracker web page loads data.json from this repo (raw.githubusercontent.com),
so committing an updated file refreshes the app for everyone. No server needed.

If a source page changes shape and parsing yields nothing useful, the script
keeps the previous data and only bumps "checked" - it never wipes the file.
"""

import json
import re
import sys
from datetime import date, datetime, timezone

import requests
from bs4 import BeautifulSoup

DATA_FILE = "data.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ClassActionTracker/1.0)"}
TIMEOUT = 30

SOURCES = [
    ("claim.ca", "https://www.claim.ca/class-actions"),
    ("canooq", "https://www.canooq.ca/class-actions"),
]


def fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text


def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def parse_listing(html, source):
    """Best-effort parse: find headings/links that look like case entries."""
    soup = BeautifulSoup(html, "html.parser")
    cases = []
    seen = set()
    # Candidate containers: article, list items, cards
    for tag in soup.find_all(["article", "li", "div"], limit=400):
        cls = " ".join(tag.get("class", [])).lower()
        if not any(k in cls for k in ("card", "case", "item", "post", "listing", "entry", "result")):
            continue
        link = tag.find("a", href=True)
        head = tag.find(["h2", "h3", "h4"])
        name = clean(head.get_text() if head else (link.get_text() if link else ""))
        if len(name) < 8 or len(name) > 140 or name in seen:
            continue
        href = link["href"] if link else ""
        if href.startswith("/"):
            base = "https://www.claim.ca" if source == "claim.ca" else "https://www.canooq.ca"
            href = base + href
        text = clean(tag.get_text())
        # Hunt for a deadline-ish date in the card text
        deadline = None
        m = re.search(
            r"(?:deadline|closes?|due|by)\s*:?\s*([A-Z][a-z]+ \d{1,2},? 20\d{2}|\d{4}-\d{2}-\d{2})",
            text,
            re.IGNORECASE,
        )
        if m:
            deadline = m.group(1)
        seen.add(name)
        cases.append({"name": name, "url": href, "deadline_hint": deadline, "source": source})
    return cases


def load_data():
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"updated": None, "checked": None, "cases": []}


def main():
    data = load_data()
    by_name = {c.get("name", "").lower(): c for c in data.get("cases", [])}
    found_any = False
    notes = []

    for source, url in SOURCES:
        try:
            html = fetch(url)
        except Exception as e:  # noqa: BLE001 - keep going on source failure
            notes.append(f"{source}: fetch failed ({e})")
            continue
        try:
            parsed = parse_listing(html, source)
        except Exception as e:  # noqa: BLE001
            notes.append(f"{source}: parse failed ({e})")
            continue
        if not parsed:
            notes.append(f"{source}: no cases parsed (page shape may have changed)")
            continue
        found_any = True
        notes.append(f"{source}: parsed {len(parsed)} candidate entries")
        for p in parsed:
            key = p["name"].lower()
            if key in by_name:
                c = by_name[key]
                # Refresh source link if we have a better one
                if p["url"] and not c.get("source_url"):
                    c["source_url"] = p["url"]
                if p["deadline_hint"] and not c.get("deadline"):
                    c["deadline_note"] = f"Spotted on {source}: {p['deadline_hint']} (unverified)"
            else:
                by_name[key] = {
                    "id": re.sub(r"[^a-z0-9]+", "-", p["name"].lower()).strip("-")[:60],
                    "name": p["name"],
                    "category": "Uncategorized",
                    "scope": "Canada-wide",
                    "status": "watch",
                    "deadline": None,
                    "deadline_note": f"New listing spotted on {source}"
                    + (f" ({p['deadline_hint']})" if p["deadline_hint"] else "")
                    + " - verify before publishing details.",
                    "claim_url": p["url"],
                    "payout": "TBD",
                    "qualifies": "See source page.",
                    "source_url": p["url"],
                    "needs_review": True,
                }

    data["cases"] = list(by_name.values())
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    data["checked"] = now
    if found_any:
        data["updated"] = date.today().isoformat()
    data["notes"] = notes

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"checked={now} cases={len(data['cases'])}")
    for n in notes:
        print(" -", n)


if __name__ == "__main__":
    sys.exit(main())
