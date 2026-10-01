"""Hiring signals: find each company's applicant-tracking board and count open roles.

Looks for Greenhouse, Lever, Ashby and Workable links on the homepage, /careers and /jobs, then reads the
board's public job feed. Writes one JSON line per company to hiring.jsonl; rerunning resumes.
"""
import csv, json, os, re, sys, threading, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
HERE = os.environ.get("FV_DIR") or os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "hiring.jsonl")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
ATS = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)"),
     "https://boards-api.greenhouse.io/v1/boards/{}/jobs", lambda d: d.get("jobs", []), lambda j: j.get("title")),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([A-Za-z0-9_.-]+)"),
     "https://api.lever.co/v0/postings/{}?mode=json", lambda d: d if isinstance(d, list) else [], lambda j: j.get("text")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)"),
     "https://api.ashbyhq.com/posting-api/job-board/{}", lambda d: d.get("jobs", []), lambda j: j.get("title")),
    ("workable", re.compile(r"apply\.workable\.com/([A-Za-z0-9_-]+)"),
     "https://apply.workable.com/api/v1/widget/accounts/{}", lambda d: d.get("jobs", []), lambda j: j.get("title")),
]
SKIP = {"embed", "api", "j", "jobs", "careers", "v1"}
ML = re.compile(r"\b(machine learning|ml|ai|research|scientist|applied|llm|data scientist|computer vision|perception|robotics)\b", re.I)
ENG = re.compile(r"\b(engineer|engineering|developer|swe|architect|founding)\b", re.I)

def fetch(url, timeout=15):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(2_000_000).decode("utf-8", "replace"), r.geturl()
    except Exception:
        return None, url

def process(row):
    site = row["website"].strip()
    if site and not site.startswith("http"):
        site = "https://" + site
    found, careers_url = {}, ""
    for path in ["", "/careers", "/jobs", "/join", "/company/careers"]:
        if not site:
            break
        page, final = fetch(site.rstrip("/") + path)
        if not page:
            continue
        if path and not careers_url and re.search(r"career|job|join|hiring|open role", page, re.I):
            careers_url = final
        for name, rx, *_ in ATS:
            for m in rx.finditer(page + " " + final):
                slug = m.group(1).strip("/").split("?")[0]
                if slug.lower() not in SKIP:
                    found.setdefault(name, slug)
        if found:
            break
    guessed = False
    if not found:
        # JS-rendered careers pages hide the board link; try the obvious board slugs instead.
        stem = row["domain"].split(".")[0] if row.get("domain") else ""
        cands = list(dict.fromkeys(x for x in [row["company_id"], stem, re.sub(r"[^a-z0-9]", "", row["company"].lower())] if x))
        for name, rx, api, getjobs, title in ATS[:3]:
            for slug in cands:
                txt, _ = fetch(api.format(slug))
                try:
                    jobs = getjobs(json.loads(txt)) if txt else []
                except ValueError:
                    jobs = []
                if jobs:
                    found, guessed = {name: slug}, True
                    break
            if found:
                break
    res = {"company_id": row["company_id"], "ats": "", "ats_slug": "", "ats_open_jobs": None, "careers_url": careers_url,
           "ats_match": "guessed slug" if guessed else ("linked from site" if found else ""),
"eng_jobs": 0, "ml_jobs": 0, "titles": [], "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    for name, rx, api, getjobs, title in ATS:
        if name not in found:
            continue
        txt, _ = fetch(api.format(found[name]))
        try:
            jobs = getjobs(json.loads(txt)) if txt else None
        except ValueError:
            jobs = None
        if jobs is None:
            continue
        titles = [title(j) or "" for j in jobs]
        res.update(ats=name, ats_slug=found[name], ats_open_jobs=len(jobs), titles=titles[:25],
                   eng_jobs=sum(bool(ENG.search(t)) for t in titles), ml_jobs=sum(bool(ML.search(t)) for t in titles))
        break
    return res

def main():
    rows = {r["company_id"]: r for r in csv.DictReader(open(os.path.join(HERE, "companies.csv")))}
    only = set(open(sys.argv[2]).read().split()) if len(sys.argv) > 2 else None
    done = {json.loads(l)["company_id"] for l in open(OUT)} if os.path.exists(OUT) else set()
    todo = [r for k, r in rows.items() if k not in done and (only is None or k in only)]
    print(len(todo), "to go", flush=True)
    lock = threading.Lock()
    def work(r):
        try:
            res = process(r)
        except Exception as e:
            print("ERR", r["company_id"], e, flush=True); return
        with lock, open(OUT, "a") as f:
            f.write(json.dumps(res) + "\n")
    with ThreadPoolExecutor(int(sys.argv[1]) if len(sys.argv) > 1 else 12) as ex:
        list(ex.map(work, todo))
    print("done", flush=True)

if __name__ == "__main__":
    main()
