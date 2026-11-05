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
from datetime import date, datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

DATA_FILE = "data.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ClassActionTracker/1.0)"}
TIMEOUT = 30

SOURCES = [
    ("claim.ca", "https://www.claim.ca/class-actions"),
    ("canooq", "https://www.canooq.ca/class-actions"),
]

# CBA National Class Action Database (cbaapps.org - note the double p;
# cbaapp.org is a dead/spam domain). Monthly browse pages are plain GETs:
# CAMonthYear.aspx?date=MM/01/YY lists that month's new filings, no form
# dance needed. Cases here are filings, mostly "not certified", so they
# only ever enter as watch entries flagged for review - no deadlines or
# payouts live in this database.
CBA_BASE = "https://cbaapps.org/ClassAction"

PROVINCES = {
    "AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba",
    "NB": "New Brunswick", "NL": "Newfoundland and Labrador",
    "NS": "Nova Scotia", "NT": "Northwest Territories", "NU": "Nunavut",
    "ON": "Ontario", "PE": "Prince Edward Island", "QC": "Quebec",
    "SK": "Saskatchewan", "YT": "Yukon", "FED": "Federal",
}


def _cba_hidden(soup):
    return {i["name"]: i.get("value", "")
            for i in soup.find_all("input", {"type": "hidden"})}


def _parse_cba_grid(soup, url):
    """Parse one CBA results grid page into case dicts."""
    cases = []
    rows = soup.find_all("tr")
    i = 0
    while i < len(rows):
        cells = [clean(c.get_text())
                 for c in rows[i].find_all(["td", "th"], recursive=False)]
        # A case's main row starts with a date like "October 30 2026".
        if cells and re.match(r"^[A-Z][a-z]+ \d{1,2} \d{4}$", cells[0]):
            title = cells[3] if len(cells) > 3 else ""
            # Some rows embed the detail block in the title cell.
            title = re.sub(r"\s*Status:.*$", "", title).strip()
            subjects = cells[2] if len(cells) > 2 else ""
            status, prov = "", ""
            j = i + 1
            while j < len(rows):
                sc = [clean(c.get_text())
                      for c in rows[j].find_all(["td", "th"], recursive=False)]
                if sc and re.match(r"^[A-Z][a-z]+ \d{1,2} \d{4}$", sc[0]):
                    break
                joined = " | ".join(sc)
                if joined.startswith("Status:"):
                    status = joined[len("Status:"):].strip()
                elif joined.startswith("Province/Territory:"):
                    prov = joined[len("Province/Territory:"):].strip()
                j += 1
            if len(title) >= 8:
                cases.append({
                    "name": title[:160],
                    "subjects": subjects,
                    "province": prov,
                    "status": status,
                    "date_received": cells[0],
                    "date_posted": cells[1] if len(cells) > 1 else "",
                    "url": url,
                })
            i = j
        else:
            i += 1
    return cases


def fetch_cba_month(year, month):
    """New filings posted in one month on the CBA database.

    Returns a list of dicts: name, subjects, province, status,
    date_received, date_posted, url. Walks the grid pager (10/page)
    via __doPostBack so months with more than 10 filings are complete.
    """
    import requests

    url = f"{CBA_BASE}/CAMonthYear.aspx?date={month:02d}/01/{year % 100:02d}"
    session = requests.Session()
    html = session.get(url, headers=HEADERS, timeout=TIMEOUT).text
    cases = _parse_cba_grid(BeautifulSoup(html, "html.parser"), url)
    # Pager links are dgrArticles$ctl14$ctlNN for pages 2..N on page 1.
    targets = sorted(set(re.findall(r"dgrArticles\$ctl14\$ctl(\d+)", html)))
    for num in targets[:5]:
        form = _cba_hidden(BeautifulSoup(html, "html.parser"))
        form["__EVENTTARGET"] = f"dgrArticles$ctl14$ctl{num}"
        form["__EVENTARGUMENT"] = ""
        html = session.post(url, data=form, headers=HEADERS,
                            timeout=TIMEOUT).text
        cases.extend(_parse_cba_grid(BeautifulSoup(html, "html.parser"), url))
    return cases


def cba_category(subjects):
    s = subjects or ""
    if "Consumer Protection" in s:
        return "Consumer"
    if "Competition" in s:
        return "Competition"
    if "Breach of Privacy" in s:
        return "Privacy"
    if "Banking" in s or "Securities" in s:
        return "Financial"
    if "Product Liability" in s or "Pharmaceutical" in s:
        return "Product liability"
    return "Uncategorized"


def make_watch_entry(name, category, scope, claim_url, deadline_note):
    return {
        "id": re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60],
        "name": name,
        "category": category,
        "scope": scope,
        "status": "watch",
        "deadline": None,
        "deadline_note": deadline_note,
        "claim_url": claim_url,
        "payout": "TBD",
        "qualifies": "See source page.",
        "source_url": claim_url,
        "needs_review": True,
    }


def fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text


def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def normalize(name):
    """Loose key for matching scraped names to existing cases.

    Strips parenthetical amounts like '($5.1M)', punctuation, and generic
    trailing suffixes so 'Truffettes de France Mislabelling' matches
    'Truffettes de France Mislabelling Class Action' instead of creating
    a duplicate entry.
    """
    n = (name or "").lower()
    n = re.sub(r"\([^)]*\)", "", n)
    n = re.sub(r"[^a-z0-9]+", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    n = re.sub(r"\s+(class action|lawsuit|settlement|litigation)$", "", n)
    return n


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

    # Collapse any existing entries that normalize to the same key
    # (e.g. a duplicate created before the matcher was fixed).
    # Prefer open cases over watch, and reviewed entries over needs_review.
    def rank(c):
        return (0 if c.get("status") == "open" else 1,
                0 if not c.get("needs_review") else 1)

    deduped = []
    seen = {}
    for c in data.get("cases", []):
        key = normalize(c.get("name", ""))
        if key in seen:
            idx = seen[key]
            if rank(c) < rank(deduped[idx]):
                deduped[idx] = c
        else:
            seen[key] = len(deduped)
            deduped.append(c)
    data["cases"] = deduped

    by_name = {normalize(c.get("name", "")): c for c in data.get("cases", [])}
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
            key = normalize(p["name"])
            if key in by_name:
                c = by_name[key]
                # Refresh source link if we have a better one
                if p["url"] and not c.get("source_url"):
                    c["source_url"] = p["url"]
                if p["deadline_hint"] and not c.get("deadline"):
                    c["deadline_note"] = f"Spotted on {source}: {p['deadline_hint']} (unverified)"
            else:
                by_name[key] = make_watch_entry(
                    p["name"],
                    "Uncategorized",
                    "Canada-wide",
                    p["url"],
                    f"New listing spotted on {source}"
                    + (f" ({p['deadline_hint']})" if p["deadline_hint"] else "")
                    + " - verify before publishing details.",
                )

    # CBA National Class Action Database: new filings from the current and
    # previous month. These are filings, not settlements, so they land as
    # watch entries flagged for review.
    today = date.today()
    prev_month = today.replace(day=1) - timedelta(days=1)
    for y, m in ((today.year, today.month), (prev_month.year, prev_month.month)):
        try:
            cba_cases = fetch_cba_month(y, m)
        except Exception as e:  # noqa: BLE001
            notes.append(f"cba: month {y}-{m:02d} fetch failed ({e})")
            continue
        if not cba_cases:
            notes.append(f"cba: no filings parsed for {y}-{m:02d}")
            continue
        found_any = True
        notes.append(f"cba: parsed {len(cba_cases)} filings for {y}-{m:02d}")
        for p in cba_cases:
            key = normalize(p["name"])
            if key in by_name:
                continue
            prov = PROVINCES.get(p["province"], "Canada-wide")
            by_name[key] = make_watch_entry(
                p["name"],
                cba_category(p["subjects"]),
                prov,
                p["url"],
                f"Listed in the CBA National Class Action Database "
                f"(received {p['date_received']}, posted {p['date_posted']}; "
                f"{prov}; status: {p['status'] or 'unknown'}). "
                f"Not yet certified - no claims open. "
                f"Verify before publishing details.",
            )

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

if __name__ == "__main__":
    sys.exit(main())
