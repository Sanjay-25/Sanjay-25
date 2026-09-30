# Task: verify post-YC funding for 1,335 YC companies

Input: `companies.csv` (one row per company, YC batches Fall 2024 to Summer 2026).
Output: `results.csv` with the columns in `output_template.csv`, one row per company, plus `sources/` for anything you save.

The question for every company: **did it raise money after YC, and how much?**
"Post-YC" means any round beyond YC's standard deal, dated after the company's batch started. Record the amount, date, round type and the source that proves it.

## Source hierarchy (follow in order)

Earlier sources are ground truth for the amount; later ones are leads only. Stop going down the list once a company is resolved at tier 1 or 2, but still record investors or framing from lower tiers if you already have them.

1. **Primary filings (the amount)**
   - SEC EDGAR full-text search, Form D and D/A. Read `totalAmountSold`, `totalOfferingAmount`, `dateOfFirstSale`, the security type, investor count, and `clarificationOfResponse`.
   - Form C for Reg CF crowdfunding; S-1, 8-K, 10-K/10-Q if public.
   - Non-US: UK Companies House SH01 share allotments and confirmation statements; the equivalent national register elsewhere (check `country`).
2. **The company's own words (the framing, stage, investors)**
   - /blog, /news, /press, /about, changelog, careers page, and the trust or security centre (often stale but real detail).
   - LinkedIn company page posts; founders' LinkedIn and X posts.
   - Launch surfaces: YC launch page, Show HN, Product Hunt.
3. **The investor side**
   - Lead investor's portfolio page and announcement post; the partner's own blog or thread, which often names the round before press does.
   - Accelerator and program directories (YC, Neo, South Park Commons, a16z speedrun). These set the default deal size.
4. **Press (confirms an announcement happened)**
   - Trade press, sector trades, the local business journal (covers rounds national press skips).
5. **Aggregators, last and as leads only**
   - Crunchbase, PitchBook, Dealroom, Tracxn, CB Insights, Wellfound.
   - Many re-publish a Form D automatically. Open the item and read its own sourcing language: "according to a Form D filed with the SEC" means it is the filing again, not an announcement.
   - `tracxn_total_usd_LEAD_ONLY` in the input is a tier-5 lead. Never report it as the amount without a higher-tier source.

## EDGAR notes (learned from a test run)

- Every request needs a `User-Agent` header with a contact, e.g. `Fast Code AI research sanjay@fastcode.ai`. Stay under 10 requests per second.
- Full-text search: `https://efts.sec.gov/LATEST/search-index?q="<phrase>"&forms=D` (add `&dateRange=custom&startdt=2024-06-01`). Hits return `_id` as `<accession>:primary_doc.xml`, plus `display_names` (legal name + CIK) and `file_date`.
- Filing XML: `https://www.sec.gov/Archives/edgar/data/<CIK without leading zeros>/<accession without dashes>/primary_doc.xml`.
- **Brand names often differ from legal names** (Firecrawl files as "SideGuide Technologies Inc."). Searching the brand name alone misses these. Search each founder's full name as a phrase too; Form D lists executive officers and directors. Try name variants without accents (Müller → Muller).
- Confirm a hit is the right company: the issuer address should match `location`, and the related persons should include a listed founder. Record the legal name.
- A Form D filed around the batch start for roughly $500K is usually YC's own investment, not a post-YC round. Read the date and amount before counting it.
- Many seed SAFE rounds never file a Form D, or file months late. "No Form D found" means go to tier 2, never "did not raise".

## YC default deal

YC's standard deal sets the baseline (check the current terms at ycombinator.com/deal before starting; as of 2025 it was $500K total). Anything beyond it counts as post-YC. Batches start roughly: Winter = January, Spring = April, Summer = June, Fall = September/October.

## Output rules

- `raised_post_yc`: Yes / No evidence found / Unclear. Use "No evidence found" rather than "No"; absence of a filing is not proof.
- `source_tier`: 1 to 5 as above. `confidence`: High (tier 1, or tier 2 stating the amount), Medium (tier 3 or 4 naming an amount), Low (tier 5 only).
- `source_quote`: the exact words or field values that support the amount, under 25 words.
- Never invent an amount, date or investor. If sources disagree, record the higher-tier one and note the conflict in `notes`.
- Checkpoint: append to `results.csv` after each company so the run can resume. Skip companies already in it.
- When finished, print a summary: counts by `raised_post_yc` and `source_tier`, and a list of companies still Unclear.

## Suggested order

1. Run the EDGAR pass for every company (company name, then each founder name).
2. For companies without a matching Form D, fetch the website's blog/news/press pages and the YC launch page, then search press.
3. Leave logged-in sources (LinkedIn, Crunchbase, PitchBook) for last and mark them as needing a browser if you cannot reach them.
