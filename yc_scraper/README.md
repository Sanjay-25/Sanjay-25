# YC Founders Scraper (Summer 2023 – Fall 2026)

Collects founder names, titles and **public** LinkedIn profile URLs for every
Y Combinator company in the Summer 2023 → Fall 2026 batches.

## Data sources (public only)

| Purpose | Source |
|---|---|
| Batch index | `https://yc-oss.github.io/api/meta.json` |
| Company list per batch | `https://yc-oss.github.io/api/batches/{slug}.json` |
| Founder details | `https://www.ycombinator.com/companies/{slug}` |

LinkedIn is **never** contacted. The script only records LinkedIn URLs that YC
itself publishes on its public company pages.

## How founder extraction works

YC company pages are Inertia.js server-rendered: the complete company payload —
including the `founders` array with `full_name`, `title`, `linkedin_url`,
`twitter_url` and `founder_bio` — is embedded in the HTML as a JSON blob in the
root element's `data-page` attribute.

Parsing that JSON is the primary path. It is more robust than CSS selectors and
needs no JavaScript engine, so **Playwright/Selenium are not required**. A
BeautifulSoup fallback (`parse_founders_from_html`) handles the case where YC
changes that structure.

## Usage

```bash
pip install requests beautifulsoup4 lxml

python yc_founders_scraper.py                       # all 12 default batches
python yc_founders_scraper.py --batch winter-2026   # one batch
python yc_founders_scraper.py --batch winter-2026 --batch summer-2026
python yc_founders_scraper.py --list-batches        # what the API offers
python yc_founders_scraper.py --limit 10 --no-resume  # smoke test
```

Key flags: `--out-dir`, `--min-delay` / `--max-delay`, `--max-retries`,
`--checkpoint-every`, `--no-resume`, `-v`.

## Output

- `output/yc_founders_2023_2026.csv` — one row per founder
- `output/yc_founders_2023_2026.json` — same data as JSON
- `output/checkpoint.jsonl` — per-company progress (enables resume)
- `output/scrape.log` — full debug log

Columns: `founder_name, title, linkedin_url, company_name, company_slug, batch,
company_website, scraped_at` plus extras (`twitter_url`, `founder_bio`,
`company_one_liner`, `company_location`, `company_team_size`, `company_status`,
`company_tags`, `company_linkedin_url`, `yc_company_url`).

## Politeness & robustness

- 1.5–2.5 s randomized delay between YC page requests; UA rotation.
- 3 retries with exponential backoff (4/8/16 s + jitter); honours `Retry-After`.
- 404s are not retried (companies do get removed from the directory).
- Checkpoint every 50 companies; re-running resumes where it left off.
- `robots.txt` respected: YC disallows `/companies?*` (query-string listing
  pages), which this script never requests. `/companies/{slug}` is allowed.

## Batch scope

Default targets are Summer 2023 → Fall 2026 (12 batches). `winter-2023` and the
placeholder `winter-2027` batch are outside the default window but can be
scraped explicitly with `--batch`.
