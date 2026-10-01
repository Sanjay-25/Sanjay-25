"""Add YC directory and job metadata to the first run's companies.csv (Fall 2024 to Summer 2026).

Adds team_size, yc_is_hiring, yc_stage, industry for every company (from YC's directory index) and
yc_open_jobs / yc_job_titles for the companies with a Yes/Unclear result (from their YC pages).
"""
import csv, html, json, os, re, sys, time, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "batch_2020_2024"))
import importlib.util
spec = importlib.util.spec_from_file_location("ycl", os.path.join(HERE, "batch_2020_2024", "fetch_yc_list.py"))
src = open(spec.origin).read().split("facets = query(")[0]   # reuse query() without running the fetch
ns = {}
exec(src, ns)
query = ns["query"]

rows = list(csv.DictReader(open(os.path.join(HERE, "companies.csv"))))
batches = sorted({r["batch"] for r in rows})
meta = {}
for b in batches:
    page = 0
    while True:
        d = query({"hitsPerPage": 1000, "page": page, "facetFilters": json.dumps([f"batch:{b}"])})
        for h in d["hits"]:
            meta[h["slug"]] = h
        page += 1
        if page >= d["nbPages"]:
            break
want = {r["company_id"] for r in csv.DictReader(open(os.path.join(HERE, "results.csv")))
        if r["raised_post_yc"] in ("Yes", "Unclear")}
cache = os.path.join(HERE, "yc_pages")
os.makedirs(cache, exist_ok=True)
jobs = {}
for cid in sorted(want):
    path = os.path.join(cache, cid + ".html")
    if not os.path.exists(path):
        for i in range(5):
            try:
                req = urllib.request.Request(f"https://www.ycombinator.com/companies/{cid}", headers={"User-Agent": "Mozilla/5.0 Chrome/126.0"})
                s = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
                if 'data-page="' in s:
                    open(path, "w").write(s)
                    break
            except Exception:
                pass
            time.sleep(3 * (i + 1))
        time.sleep(0.4)
    if os.path.exists(path):
        m = re.search(r'data-page="([^"]+)"', open(path).read())
        p = json.loads(html.unescape(m.group(1)))["props"] if m else {}
        jobs[cid] = p.get("jobPostings") or []
new = ["team_size", "yc_is_hiring", "yc_open_jobs", "yc_job_titles", "yc_stage", "industry"]
for r in rows:
    h = meta.get(r["company_id"], {})
    j = jobs.get(r["company_id"])
    r.update({"team_size": h.get("team_size") or "", "yc_is_hiring": ("Yes" if h.get("isHiring") else "No") if h else "",
              "yc_open_jobs": len(j) if j is not None else "", "yc_job_titles": "; ".join((x.get("title") or "") for x in (j or [])[:12]),
              "yc_stage": h.get("stage") or "", "industry": h.get("industry") or ""})
cols = list(rows[0].keys())
with open(os.path.join(HERE, "companies.csv"), "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
print(len(meta), "directory records;", len(jobs), "YC pages read for", len(want), "Yes/Unclear companies")
