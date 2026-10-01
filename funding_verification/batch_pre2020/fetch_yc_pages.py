"""Fetch each company's YC page (cached in yc_pages/) and build companies.csv plus hiring metadata.

companies.csv uses the same columns as the first run, plus: team_size, yc_is_hiring, yc_open_jobs,
yc_job_titles, yc_stage, industry. Rerunning reuses the cache.
"""
import csv, html, json, os, re, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
import threading
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "yc_pages")
os.makedirs(CACHE, exist_ok=True)
lock, last = threading.Lock(), [0.0]

def fetch(url):
    for i in range(6):
        with lock:
            wait = last[0] + 0.35 - time.time()
            if wait > 0: time.sleep(wait)
            last[0] = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/126.0"})
            s = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
            if 'data-page="' in s:
                return s
        except Exception:
            pass
        time.sleep(3 * (i + 1))
    return None

def props_of(s):
    m = re.search(r'data-page="([^"]+)"', s or "")
    return json.loads(html.unescape(m.group(1)))["props"] if m else None

def lit(v):
    """YC serialises some nested fields as Python-literal strings."""
    if isinstance(v, str) and v[:1] in "[{":
        import ast
        try: return ast.literal_eval(v)
        except Exception: return []
    return v or []

hits = json.load(open(os.path.join(HERE, "yc_active_pre2020.json")))
def get(h):
    path = os.path.join(CACHE, h["slug"] + ".html")
    if not os.path.exists(path):
        s = fetch(f"https://www.ycombinator.com/companies/{h['slug']}")
        if s:
            open(path, "w").write(s)
    return h["slug"]
with ThreadPoolExecutor(4) as ex:
    list(ex.map(get, hits))

cols = ["company_id","company","batch","yc_status","website","domain","yc_page","one_liner","location","country","ai_native",
        "company_linkedin","company_x","founders","founder_linkedins","founder_xs","tracxn_total_usd_LEAD_ONLY","in_strict_funnel",
        "team_size","yc_is_hiring","yc_open_jobs","yc_job_titles","yc_stage","industry"]
out, missing = [], []
for h in hits:
    path = os.path.join(CACHE, h["slug"] + ".html")
    p = props_of(open(path).read()) if os.path.exists(path) else None
    c = (p or {}).get("company", {}) or {}
    founders = lit(c.get("founders"))
    jobs = (p or {}).get("jobPostings") or []
    if p is None:
        missing.append(h["slug"])
    web = h.get("website") or c.get("website") or ""
    dom = re.sub(r"^https?://(www\.)?", "", web).split("/")[0].lower()
    out.append({"company_id": h["slug"], "company": h["name"], "batch": h["batch"], "yc_status": h["status"],
        "website": web, "domain": dom, "yc_page": f"https://www.ycombinator.com/companies/{h['slug']}",
        "one_liner": (h.get("one_liner") or "").replace("\n", " "), "location": h.get("all_locations") or "",
        "country": c.get("country") or "", "ai_native": "", "company_linkedin": c.get("linkedin_url") or "",
        "company_x": c.get("twitter_url") or "",
        "founders": "; ".join(f.get("full_name", "") for f in founders if isinstance(f, dict)),
        "founder_linkedins": "; ".join(f.get("linkedin_url") or "" for f in founders if isinstance(f, dict)),
        "founder_xs": "; ".join(f.get("twitter_url") or "" for f in founders if isinstance(f, dict)),
        "tracxn_total_usd_LEAD_ONLY": "", "in_strict_funnel": "",
        "team_size": h.get("team_size") or c.get("team_size") or "", "yc_is_hiring": "Yes" if h.get("isHiring") else "No",
        "yc_open_jobs": len(jobs), "yc_job_titles": "; ".join((j.get("title") or "") for j in jobs[:12]),
        "yc_stage": h.get("stage") or "", "industry": h.get("industry") or ""})
with open(os.path.join(HERE, "companies.csv"), "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(out)
print(len(out), "companies;", len(missing), "YC pages missing:", missing[:10])
