# Class Action Tracker - daily data updater

Free daily refresh for the Canada Class Action Tracker web page. No server:
a GitHub Actions cron runs `scrape.py` once a day, updates `data.json`,
and the tracker page loads that file straight from the repo.

## Setup (about 5 minutes)

1. Create a **public** GitHub repo, e.g. `class-action-tracker-data`.
2. Upload these files keeping the paths:
   - `scrape.py`
   - `data.json`
   - `requirements.txt`
   - `.github/workflows/daily.yml`
   (On github.com: Add file > Upload files, drag the whole folder contents.)
3. Open the repo's **Actions** tab and enable workflows if prompted.
4. Optional: run it once manually (Actions > "Daily class action data refresh" > Run workflow)
   to confirm `data.json` gets a fresh `checked` timestamp.
5. Send the repo URL to your assistant so the tracker page can be pointed at:
   `https://raw.githubusercontent.com/<you>/<repo>/main/data.json`

## How it works

- `scrape.py` fetches the claim.ca and canooq.ca listing pages, extracts case
  names/links/deadline hints, merges them into `data.json`, and stamps `checked`.
- Existing verified cases are never overwritten blindly: the script only fills in
  blanks and flags brand-new listings with `"needs_review": true`.
- If a source page changes shape and parsing fails, the old data is kept as-is.
- The workflow commits only when `data.json` actually changed.

## Files

| File | Purpose |
| ---- | ------- |
| `data.json` | The live dataset the tracker page reads (`updated`, `checked`, `cases`) |
| `scrape.py` | The scraper/merger |
| `.github/workflows/daily.yml` | Daily 09:00 UTC cron + manual trigger |
| `requirements.txt` | `requests`, `beautifulsoup4` |
