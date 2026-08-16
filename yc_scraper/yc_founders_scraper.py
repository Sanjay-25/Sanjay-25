#!/usr/bin/env python3
"""
Y Combinator founder scraper (Summer 2023 -> Fall 2026).

Collects founder names, titles and public LinkedIn profile URLs for every
company in the targeted YC batches, using only publicly available data:

  1. Company lists come from the open yc-oss API (https://yc-oss.github.io/api/).
  2. Founder details come from each company's public YC directory page
     (https://www.ycombinator.com/companies/{slug}).

The YC company page is an Inertia.js server-rendered page: the full company
payload (including the founders array) is embedded in the HTML as a JSON blob
in the `data-page` attribute of the app root element. Parsing that JSON is far
more robust than CSS selectors and requires no JavaScript engine, so no
Playwright/Selenium fallback is necessary. A BeautifulSoup-based fallback is
still implemented in case YC changes that structure.

LinkedIn itself is never contacted -- we only record URLs that YC publishes.

Usage:
    python yc_founders_scraper.py                      # all default batches
    python yc_founders_scraper.py --batch winter-2026  # a single batch
    python yc_founders_scraper.py --batch winter-2026 --batch summer-2026
    python yc_founders_scraper.py --list-batches       # show what the API offers
    python yc_founders_scraper.py --limit 10 --no-resume   # quick smoke test
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import logging
import random
import re
import sys
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

YC_OSS_META = "https://yc-oss.github.io/api/meta.json"
YC_OSS_BATCH = "https://yc-oss.github.io/api/batches/{slug}.json"
YC_COMPANY_URL = "https://www.ycombinator.com/companies/{slug}"

# Batches targeted by default: Summer 2023 through the latest 2026 batch.
# Winter 2023 (pre-dates the "Summer 2023 onwards" window) and Winter 2027
# (a single placeholder company) are excluded by default but can be scraped
# explicitly with --batch winter-2023 / --batch winter-2027.
DEFAULT_BATCHES = [
    "summer-2023",
    "winter-2024",
    "summer-2024",
    "fall-2024",
    "winter-2025",
    "spring-2025",
    "summer-2025",
    "fall-2025",
    "winter-2026",
    "spring-2026",
    "summer-2026",
    "fall-2026",
]

# Politeness: YC's robots.txt allows /companies/{slug} (only the query-string
# /companies?* listing endpoints are disallowed). We still throttle hard.
MIN_DELAY = 1.5
MAX_DELAY = 2.5
MAX_RETRIES = 3
BACKOFF_BASE = 4.0          # seconds; 4, 8, 16 with jitter
REQUEST_TIMEOUT = 30
CHECKPOINT_EVERY = 50

# Realistic desktop User-Agents, rotated per company request.
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

CSV_COLUMNS = [
    "founder_name",
    "title",
    "linkedin_url",
    "company_name",
    "company_slug",
    "batch",
    "company_website",
    "scraped_at",
    # Extra public fields that are cheap to collect:
    "twitter_url",
    "founder_bio",
    "company_one_liner",
    "company_location",
    "company_team_size",
    "company_status",
    "company_tags",
    "company_linkedin_url",
    "yc_company_url",
]

log = logging.getLogger("yc_scraper")


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------

@dataclass
class Founder:
    """One row of output: a single founder at a single company."""
    founder_name: str
    title: str
    linkedin_url: str
    company_name: str
    company_slug: str
    batch: str
    company_website: str
    scraped_at: str
    twitter_url: str = ""
    founder_bio: str = ""
    company_one_liner: str = ""
    company_location: str = ""
    company_team_size: str = ""
    company_status: str = ""
    company_tags: str = ""
    company_linkedin_url: str = ""
    yc_company_url: str = ""


@dataclass
class CompanyResult:
    """Per-company scrape outcome, persisted to the checkpoint file."""
    slug: str
    batch: str
    status: str                      # ok | no_founders | not_found | error
    founders: list[dict] = field(default_factory=list)
    error: str = ""


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def setup_logging(log_file: Path, verbose: bool = False) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    log.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    log.addHandler(console)

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG)
    log.addHandler(fh)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_linkedin(url: str | None) -> str:
    """
    Normalize the inconsistent LinkedIn URLs YC stores.

    YC founder records contain a mix of `linkedin.com/in/x`,
    `https://www.linkedin.com/in/x/`, `http://...`, bare handles and junk.
    We emit a canonical `https://www.linkedin.com/in/<handle>` where possible
    and drop anything that clearly is not a personal profile URL.
    """
    if not url:
        return ""
    url = str(url).strip()
    if not url or url.lower() in {"n/a", "none", "-"}:
        return ""

    # Strip a leading scheme/host so we can rebuild it consistently.
    cleaned = re.sub(r"^\s*(https?:)?//", "", url, flags=re.I)
    cleaned = re.sub(r"^([a-z0-9-]+\.)*linkedin\.com/", "", cleaned, flags=re.I)

    # Bare handle (e.g. "sarang-zambare") -> treat as /in/ profile.
    if "/" not in cleaned and re.fullmatch(r"[A-Za-z0-9._%\-]+", cleaned):
        return f"https://www.linkedin.com/in/{cleaned}"

    m = re.search(r"(in|pub)/([^/?#\s]+)", cleaned, flags=re.I)
    if m:
        handle = m.group(2).rstrip("/")
        return f"https://www.linkedin.com/in/{handle}"

    # Company / school / other LinkedIn URL -> not a founder profile.
    if re.match(r"(company|school|showcase)/", cleaned, flags=re.I):
        return ""

    # Unrecognised but linkedin-looking: keep the original rather than lose data.
    return url if "linkedin.com" in url.lower() else ""


def polite_sleep(min_delay: float, max_delay: float) -> None:
    """Rate limit with random jitter so requests are not perfectly periodic."""
    time.sleep(random.uniform(min_delay, max_delay))


# --------------------------------------------------------------------------
# HTTP layer
# --------------------------------------------------------------------------

class Fetcher:
    """Thin requests wrapper with retries, backoff and UA rotation."""

    def __init__(self, max_retries: int = MAX_RETRIES, timeout: int = REQUEST_TIMEOUT):
        self.session = requests.Session()
        self.max_retries = max_retries
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "keep-alive",
        }

    def get(self, url: str, expect_json: bool = False) -> tuple[int, Any]:
        """
        Return (status_code, payload).

        payload is parsed JSON when expect_json is True, otherwise raw text.
        Returns (0, None) if every attempt failed at the transport level, and
        (404, None) for a definitive not-found (no retry -- companies do get
        removed from the YC directory).
        """
        last_status = 0
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.get(url, headers=self._headers(), timeout=self.timeout)
                last_status = resp.status_code

                if resp.status_code == 404:
                    return 404, None

                if resp.status_code == 429 or resp.status_code >= 500:
                    # Honour Retry-After when the server sends it.
                    retry_after = resp.headers.get("Retry-After")
                    wait = float(retry_after) if (retry_after or "").isdigit() else \
                        BACKOFF_BASE * (2 ** (attempt - 1))
                    wait += random.uniform(0, 1.5)
                    log.warning("HTTP %s for %s (attempt %d/%d), sleeping %.1fs",
                                resp.status_code, url, attempt, self.max_retries, wait)
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                return resp.status_code, (resp.json() if expect_json else resp.text)

            except (requests.RequestException, ValueError) as exc:
                wait = BACKOFF_BASE * (2 ** (attempt - 1)) + random.uniform(0, 1.5)
                log.warning("Request error for %s (attempt %d/%d): %s -- retry in %.1fs",
                            url, attempt, self.max_retries, exc, wait)
                if attempt < self.max_retries:
                    time.sleep(wait)

        log.error("Giving up on %s after %d attempts", url, self.max_retries)
        return last_status, None


# --------------------------------------------------------------------------
# yc-oss API: batch + company discovery
# --------------------------------------------------------------------------

def fetch_batch_index(fetcher: Fetcher) -> dict[str, dict]:
    """Return {slug: {name, count, api}} for every batch the API knows about."""
    status, data = fetcher.get(YC_OSS_META, expect_json=True)
    if not data:
        raise RuntimeError(f"Could not fetch yc-oss meta.json (HTTP {status})")
    log.info("yc-oss API last updated: %s", data.get("last_updated", "unknown"))
    return data.get("batches", {})


def fetch_companies(fetcher: Fetcher, batch_slug: str) -> list[dict]:
    """Return the company list for one batch, or [] if the batch failed."""
    status, data = fetcher.get(YC_OSS_BATCH.format(slug=batch_slug), expect_json=True)
    if not isinstance(data, list):
        log.error("Batch %s could not be fetched (HTTP %s)", batch_slug, status)
        return []
    log.info("Batch %-14s -> %d companies", batch_slug, len(data))
    return data


# --------------------------------------------------------------------------
# YC company page parsing
# --------------------------------------------------------------------------

def extract_inertia_payload(page_html: str) -> dict | None:
    """
    Pull the Inertia.js `data-page` JSON blob out of the company page HTML.

    This is the primary extraction path: the blob holds the same company object
    the page renders from, including the founders array with LinkedIn URLs.
    """
    m = re.search(r'data-page="([^"]+)"', page_html)
    if not m:
        return None
    try:
        return json.loads(html.unescape(m.group(1)))
    except json.JSONDecodeError as exc:
        log.debug("data-page JSON decode failed: %s", exc)
        return None


def parse_founders_from_json(payload: dict) -> tuple[dict, list[dict]]:
    """Return (company_dict, founders_list) from an Inertia payload."""
    company = (payload.get("props") or {}).get("company") or {}
    founders = company.get("founders") or []
    return company, [f for f in founders if isinstance(f, dict)]


def parse_founders_from_html(page_html: str) -> list[dict]:
    """
    Fallback extraction if the `data-page` blob disappears.

    Walks the rendered founder cards and pairs each displayed name with the
    nearest LinkedIn link. Deliberately loose about class names, since those
    are the parts most likely to change.
    """
    soup = BeautifulSoup(page_html, "lxml")
    founders: list[dict] = []
    seen: set[str] = set()

    for link in soup.select('a[href*="linkedin.com/in/"]'):
        card = link
        # Climb a few levels to find a container that also holds the name.
        for _ in range(5):
            card = card.parent
            if card is None:
                break
            text = card.get_text(" ", strip=True)
            if not text:
                continue
            # A founder name is typically the first bold/heading-ish text.
            name_el = card.find(["h3", "h4", "b", "strong"])
            if name_el and name_el.get_text(strip=True):
                name = name_el.get_text(strip=True)
                if name and name not in seen:
                    seen.add(name)
                    founders.append({
                        "full_name": name,
                        "title": "",
                        "linkedin_url": link.get("href", ""),
                        "twitter_url": "",
                        "founder_bio": "",
                    })
                break
    if founders:
        log.debug("HTML fallback recovered %d founder(s)", len(founders))
    return founders


def scrape_company(fetcher: Fetcher, company: dict) -> CompanyResult:
    """Fetch one YC company page and extract its founders."""
    slug = company.get("slug", "")
    batch = company.get("batch", "")
    url = company.get("url") or YC_COMPANY_URL.format(slug=slug)

    status, page_html = fetcher.get(url)
    if status == 404:
        log.info("  %-32s 404 (removed from directory)", slug)
        return CompanyResult(slug=slug, batch=batch, status="not_found")
    if not page_html:
        return CompanyResult(slug=slug, batch=batch, status="error",
                             error=f"HTTP {status}")

    payload = extract_inertia_payload(page_html)
    if payload:
        page_company, raw_founders = parse_founders_from_json(payload)
    else:
        log.warning("  %-32s no data-page blob; using HTML fallback", slug)
        page_company, raw_founders = {}, parse_founders_from_html(page_html)

    if not raw_founders:
        log.info("  %-32s no founders listed", slug)
        return CompanyResult(slug=slug, batch=batch, status="no_founders")

    # Prefer richer values from the live page, fall back to the API record.
    def pick(key: str, default: Any = "") -> Any:
        return page_company.get(key) or company.get(key) or default

    tags = pick("tags", []) or []
    scraped_at = now_iso()

    rows: list[dict] = []
    for f in raw_founders:
        name = (f.get("full_name") or "").strip()
        if not name:
            continue
        rows.append(asdict(Founder(
            founder_name=name,
            title=(f.get("title") or "").strip(),
            linkedin_url=normalize_linkedin(f.get("linkedin_url")),
            company_name=pick("name", slug),
            company_slug=slug,
            batch=pick("batch_name") or batch,
            company_website=pick("website"),
            scraped_at=scraped_at,
            twitter_url=(f.get("twitter_url") or "").strip(),
            founder_bio=(f.get("founder_bio") or "").strip(),
            company_one_liner=pick("one_liner"),
            company_location=pick("location") or company.get("all_locations", ""),
            company_team_size=str(pick("team_size", "") or ""),
            company_status=company.get("status", ""),
            company_tags="; ".join(t for t in tags if isinstance(t, str)),
            company_linkedin_url=page_company.get("linkedin_url") or "",
            yc_company_url=url,
        )))

    n_li = sum(1 for r in rows if r["linkedin_url"])
    log.info("  %-32s %d founder(s), %d with LinkedIn", slug, len(rows), n_li)
    return CompanyResult(slug=slug, batch=batch, status="ok", founders=rows)


# --------------------------------------------------------------------------
# Checkpointing
# --------------------------------------------------------------------------

def load_checkpoint(path: Path) -> dict[str, CompanyResult]:
    """Reload previously scraped companies so an interrupted run can resume."""
    if not path.exists():
        return {}
    done: dict[str, CompanyResult] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                done[rec["slug"]] = CompanyResult(**rec)
            except (json.JSONDecodeError, KeyError, TypeError):
                continue  # tolerate a truncated final line from a hard kill
    if done:
        log.info("Resuming: %d companies already in checkpoint %s", len(done), path.name)
    return done


def append_checkpoint(path: Path, results: Iterable[CompanyResult]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
        fh.flush()


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def write_outputs(rows: list[dict], out_dir: Path, csv_name: str, json_name: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    csv_path = out_dir / csv_name
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    log.info("Wrote %s (%d rows)", csv_path, len(rows))

    json_path = out_dir / json_name
    json_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Wrote %s", json_path)


def print_summary(rows: list[dict], results: list[CompanyResult],
                  failed_batches: list[str], elapsed: float) -> None:
    by_status: dict[str, int] = {}
    for r in results:
        by_status[r.status] = by_status.get(r.status, 0) + 1

    with_li = [r for r in rows if r["linkedin_url"]]
    unique_people = {(r["founder_name"].lower(), r["linkedin_url"]) for r in rows}

    print("\n" + "=" * 62)
    print("  YC FOUNDER SCRAPE SUMMARY")
    print("=" * 62)
    print(f"  Companies processed        : {len(results)}")
    print(f"    ok (founders found)      : {by_status.get('ok', 0)}")
    print(f"    no founders listed       : {by_status.get('no_founders', 0)}")
    print(f"    404 / removed            : {by_status.get('not_found', 0)}")
    print(f"    errors                   : {by_status.get('error', 0)}")
    print(f"  Total founders             : {len(rows)}")
    print(f"  Unique founder identities  : {len(unique_people)}")
    pct = (100.0 * len(with_li) / len(rows)) if rows else 0.0
    print(f"  With LinkedIn URL          : {len(with_li)}  ({pct:.1f}%)")
    print(f"  Without LinkedIn URL       : {len(rows) - len(with_li)}")
    print(f"  Elapsed                    : {elapsed / 60:.1f} min")

    print("\n  Per batch:")
    batches: dict[str, dict[str, int]] = {}
    for r in results:
        b = batches.setdefault(r.batch or "unknown",
                               {"companies": 0, "founders": 0, "linkedin": 0})
        b["companies"] += 1
        b["founders"] += len(r.founders)
        b["linkedin"] += sum(1 for f in r.founders if f.get("linkedin_url"))
    for name in sorted(batches):
        s = batches[name]
        print(f"    {name:<16} {s['companies']:>5} companies  "
              f"{s['founders']:>5} founders  {s['linkedin']:>5} linkedin")

    if failed_batches:
        print(f"\n  FAILED BATCHES: {', '.join(failed_batches)}")
    else:
        print("\n  Failed batches: none")

    errs = [r for r in results if r.status == "error"]
    if errs:
        print(f"\n  Companies with errors ({len(errs)}), first 15:")
        for r in errs[:15]:
            print(f"    {r.slug:<32} {r.error}")
    print("=" * 62 + "\n")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Scrape YC founder names + public LinkedIn URLs from YC company pages.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--batch", action="append", metavar="SLUG",
                   help="Batch slug to scrape (repeatable). Default: all 2023-2026 batches.")
    p.add_argument("--list-batches", action="store_true",
                   help="List batches available from the yc-oss API and exit.")
    p.add_argument("--out-dir", type=Path, default=Path("output"))
    p.add_argument("--csv-name", default="yc_founders_2023_2026.csv")
    p.add_argument("--json-name", default="yc_founders_2023_2026.json")
    p.add_argument("--checkpoint", type=Path, default=None,
                   help="Checkpoint JSONL path. Default: <out-dir>/checkpoint.jsonl")
    p.add_argument("--checkpoint-every", type=int, default=CHECKPOINT_EVERY)
    p.add_argument("--min-delay", type=float, default=MIN_DELAY)
    p.add_argument("--max-delay", type=float, default=MAX_DELAY)
    p.add_argument("--max-retries", type=int, default=MAX_RETRIES)
    p.add_argument("--limit", type=int, default=None,
                   help="Only process the first N companies (smoke testing).")
    p.add_argument("--no-resume", action="store_true",
                   help="Ignore and overwrite any existing checkpoint.")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(args.out_dir / "scrape.log", args.verbose)

    fetcher = Fetcher(max_retries=args.max_retries)

    # --- discover batches -------------------------------------------------
    try:
        available = fetch_batch_index(fetcher)
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1

    if args.list_batches:
        for slug in sorted(available, key=lambda s: available[s].get("name", s)):
            print(f"{slug:<18} {available[slug].get('name',''):<16} "
                  f"{available[slug].get('count', 0):>5} companies")
        return 0

    wanted = args.batch or DEFAULT_BATCHES
    unknown = [b for b in wanted if b not in available]
    for b in unknown:
        log.error("Unknown batch slug: %s (use --list-batches)", b)
    wanted = [b for b in wanted if b in available]
    if not wanted:
        log.error("No valid batches to scrape.")
        return 1
    log.info("Target batches (%d): %s", len(wanted), ", ".join(wanted))

    # --- collect company list --------------------------------------------
    companies: list[dict] = []
    failed_batches: list[str] = []
    for slug in wanted:
        batch_companies = fetch_companies(fetcher, slug)
        if not batch_companies:
            failed_batches.append(slug)
            continue
        companies.extend(batch_companies)

    # A company can appear in more than one batch feed; keep the first record.
    seen_slugs: set[str] = set()
    unique_companies = []
    for c in companies:
        s = c.get("slug")
        if s and s not in seen_slugs:
            seen_slugs.add(s)
            unique_companies.append(c)
    companies = unique_companies

    if args.limit:
        companies = companies[: args.limit]
    log.info("Total companies to process: %d", len(companies))

    # --- resume from checkpoint ------------------------------------------
    ckpt_path = args.checkpoint or (args.out_dir / "checkpoint.jsonl")
    if args.no_resume and ckpt_path.exists():
        ckpt_path.unlink()
    done = load_checkpoint(ckpt_path)

    pending = [c for c in companies if c.get("slug") not in done]
    log.info("Already done: %d | Remaining: %d", len(companies) - len(pending), len(pending))

    est_min = len(pending) * ((args.min_delay + args.max_delay) / 2 + 0.4) / 60
    log.info("Estimated runtime for remaining companies: ~%.0f min", est_min)

    # --- scrape -----------------------------------------------------------
    start = time.time()
    buffer: list[CompanyResult] = []
    try:
        for i, company in enumerate(pending, 1):
            slug = company.get("slug", "?")
            log.info("[%d/%d] %s (%s)", i, len(pending), slug, company.get("batch", ""))
            try:
                result = scrape_company(fetcher, company)
            except Exception as exc:                       # never lose the run
                log.exception("Unexpected error on %s: %s", slug, exc)
                result = CompanyResult(slug=slug, batch=company.get("batch", ""),
                                       status="error", error=repr(exc))
            done[slug] = result
            buffer.append(result)

            if len(buffer) >= args.checkpoint_every:
                append_checkpoint(ckpt_path, buffer)
                log.info("--- checkpoint: %d companies saved ---", len(done))
                buffer.clear()

            if i < len(pending):
                polite_sleep(args.min_delay, args.max_delay)
    except KeyboardInterrupt:
        log.warning("Interrupted by user -- flushing checkpoint before exit.")
    finally:
        if buffer:
            append_checkpoint(ckpt_path, buffer)
            buffer.clear()

    # --- outputs ----------------------------------------------------------
    results = list(done.values())
    rows = [f for r in results for f in r.founders]
    rows.sort(key=lambda r: (r["batch"], r["company_name"].lower(), r["founder_name"]))

    write_outputs(rows, args.out_dir, args.csv_name, args.json_name)
    print_summary(rows, results, failed_batches, time.time() - start)
    return 0


if __name__ == "__main__":
    sys.exit(main())
