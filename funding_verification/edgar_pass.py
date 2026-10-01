"""Tier 1 pass: SEC EDGAR full-text search (Form D / D/A) for every company.

For each company, searches the brand name and each founder's full name as a
phrase, fetches every hit's primary_doc.xml (cached in sources/edgar/), parses
it, and keeps filings whose related persons include a listed founder. Filings
that only match on issuer name are kept separately as unconfirmed candidates.

Checkpoints one JSON line per company to edgar_pass.jsonl; rerunning resumes.
"""
import csv
import json
import os
import re
import sys
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

# FV_DIR points a run at another working folder (companies.csv in, results out).
HERE = os.environ.get("FV_DIR") or os.path.dirname(os.path.abspath(__file__))
UA = "Fast Code AI research sanjay@fastcode.ai"
SRC_DIR = os.path.join(HERE, "sources", "edgar")
OUT = os.path.join(HERE, "edgar_pass.jsonl")
STARTDT = os.environ.get("EDGAR_STARTDT", "2024-01-01")
ENDDT = time.strftime("%Y-%m-%d")
os.makedirs(SRC_DIR, exist_ok=True)

# Global rate limiter: stay under 10 req/s across threads.
_lock = threading.Lock()
_last = [0.0]
MIN_GAP = 0.125


def _throttle():
    with _lock:
        wait = _last[0] + MIN_GAP - time.time()
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()


def get(url, tries=5):
    for i in range(tries):
        _throttle()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(2 ** i)
        except Exception:
            time.sleep(2 ** i)
    raise RuntimeError("failed: " + url)


def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm_tokens(s):
    s = strip_accents(s).lower()
    s = re.sub(r"\(.*?\)", " ", s)
    return re.findall(r"[a-z0-9]+", s)


def founder_queries(name):
    """Full-name phrase variants for a founder."""
    base = re.sub(r"\s+", " ", re.sub(r"\(.*?\)", " ", name)).strip()
    out = []
    if len(base.split()) >= 2:
        out.append(base)
        plain = strip_accents(base)
        if plain != base:
            out.append(plain)
    # Nickname in parentheses, e.g. "Najmuzzaman (Nazz) Mohammad" -> "Nazz Mohammad"
    m = re.match(r"^\s*\S+\s+\(([^)]+)\)\s+(.+)$", name)
    if m and len(m.group(1).split()) == 1:
        out.append(strip_accents(f"{m.group(1)} {m.group(2)}"))
    return list(dict.fromkeys(out))


def search(phrase, max_pages=3):
    q = urllib.parse.quote(f'"{phrase}"')
    raw = []
    for page in range(max_pages):
        url = (f"https://efts.sec.gov/LATEST/search-index?q={q}&forms=D&dateRange=custom"
               f"&startdt={STARTDT}&enddt={ENDDT}&from={page * 100}")
        txt = get(url)
        try:
            data = json.loads(txt) if txt else {}
        except ValueError:
            data = {}
        got = data.get("hits", {}).get("hits", [])
        raw += got
        if len(got) < 100:
            break
    hits = []
    for h in raw:
        s = h["_source"]
        hits.append({
            "adsh": s.get("adsh"),
            "cik": (s.get("ciks") or [""])[0],
            "display_name": (s.get("display_names") or [""])[0],
            "file_date": s.get("file_date"),
            "form": s.get("form"),
            "file_num": (s.get("file_num") or [""])[0],
            "biz_locations": s.get("biz_locations"),
        })
    return hits


def t(el, path):
    x = el.find(path)
    return x.text.strip() if x is not None and x.text else ""


def fetch_filing(hit):
    adsh = hit["adsh"]
    path = os.path.join(SRC_DIR, adsh + ".xml")
    if os.path.exists(path):
        xml = open(path).read()
    else:
        cik = str(int(hit["cik"]))
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{adsh.replace('-', '')}/primary_doc.xml"
        xml = get(url)
        if not xml:
            return None
        with open(path, "w") as f:
            f.write(xml)
    xml = re.sub(r'\sxmlns(:\w+)?="[^"]+"', "", xml, count=5)
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    od = root.find("offeringData")
    if od is None:
        return None
    iss = root.find("primaryIssuer")
    persons = []
    for p in root.findall("relatedPersonsList/relatedPersonInfo"):
        persons.append(" ".join(x for x in [t(p, "relatedPersonName/firstName"), t(p, "relatedPersonName/middleName"),
                                            t(p, "relatedPersonName/lastName")] if x))
    sec = od.find("typesOfSecuritiesOffered")
    sec_types = []
    if sec is not None:
        for child in sec:
            if child.tag.startswith("is") and (child.text or "").strip().lower() == "true":
                sec_types.append(child.tag[2:].replace("Type", ""))
        desc = t(sec, "descriptionOfOtherType")
        if desc:
            sec_types.append(f"Other: {desc}")
    dfs = t(od, "typeOfFiling/dateOfFirstSale/value")
    if not dfs and t(od, "typeOfFiling/dateOfFirstSale/yetToOccur"):
        dfs = "yetToOccur"
    return {
        "adsh": adsh,
        "cik": hit["cik"],
        "file_date": hit["file_date"],
        "file_num": hit["file_num"],
        "form": hit["form"],
        "entity_name": t(iss, "entityName") if iss is not None else hit["display_name"],
        "city": t(iss, "issuerAddress/city") if iss is not None else "",
        "state": t(iss, "issuerAddress/stateOrCountry") if iss is not None else "",
        "is_amendment": t(od, "typeOfFiling/newOrAmendment/isAmendment").lower() == "true",
        "previous_accession": t(od, "typeOfFiling/newOrAmendment/previousAccessionNumber"),
        "date_first_sale": dfs,
        "security_types": sec_types,
        "total_offering_amount": t(od, "offeringSalesAmounts/totalOfferingAmount"),
        "total_amount_sold": t(od, "offeringSalesAmounts/totalAmountSold"),
        "total_remaining": t(od, "offeringSalesAmounts/totalRemaining"),
        "clarification": t(od, "offeringSalesAmounts/clarificationOfResponse"),
        "investor_count": t(od, "investors/totalNumberAlreadyInvested"),
        "related_persons": persons,
    }


def person_matches(founder, person):
    f, p = norm_tokens(founder), norm_tokens(person)
    if len(f) < 2 or len(p) < 2:
        return False
    if f[-1] != p[-1]:
        return False
    return f[0] == p[0] or (len(f[0]) > 2 and len(p[0]) > 2 and (f[0].startswith(p[0]) or p[0].startswith(f[0])))


SUFFIX = {"inc", "corp", "corporation", "co", "llc", "ltd", "limited", "company", "the", "pbc", "technologies",
          "technology", "labs", "lab", "ai", "hq", "software", "systems", "holdings", "group", "gmbh", "sas", "plc"}


def name_core(s):
    return [x for x in norm_tokens(s) if x not in SUFFIX]


def process(row):
    founders = [f.strip() for f in row["founders"].split(";") if f.strip()]
    queries = [row["company"].strip()]
    for f in founders:
        queries += founder_queries(f)
    queries = list(dict.fromkeys(q for q in queries if q))
    hits = {}
    for q in queries:
        for h in search(q):
            if h["adsh"]:
                hits.setdefault(h["adsh"], dict(h, queries=[]))["queries"].append(q)
    # Fetch filings for founder-name hits, and for company-name hits whose
    # issuer name matches the brand core (cheap filter before fetching).
    brand = name_core(row["company"])
    matched, candidates = [], []
    for adsh, h in hits.items():
        founder_hit = any(q != row["company"].strip() for q in h["queries"])
        issuer_core = name_core(re.sub(r"\(CIK \d+\)", "", h["display_name"]))
        brand_hit = bool(brand) and issuer_core[: len(brand)] == brand
        if not (founder_hit or brand_hit):
            continue
        fl = fetch_filing(h)
        if not fl or "PooledInvestmentFund" in fl["security_types"]:
            continue
        fl["queries"] = h["queries"]
        fl["matched_founders"] = sorted({f for f in founders for p in fl["related_persons"] if person_matches(f, p)})
        if fl["matched_founders"]:
            matched.append(fl)
        elif brand_hit:
            candidates.append(fl)
    return {
        "company_id": row["company_id"],
        "company": row["company"],
        "batch": row["batch"],
        "queries": queries,
        "n_hits": len(hits),
        "matched": sorted(matched, key=lambda x: x["file_date"]),
        "name_only_candidates": sorted(candidates, key=lambda x: x["file_date"]),
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def main():
    rows = list(csv.DictReader(open(os.path.join(HERE, "companies.csv"))))
    uniq = {}
    for r in rows:
        uniq.setdefault(r["company_id"], r)
    done = set()
    if os.path.exists(OUT):
        for line in open(OUT):
            try:
                done.add(json.loads(line)["company_id"])
            except ValueError:
                pass
    todo = [r for k, r in uniq.items() if k not in done]
    print(f"{len(uniq)} companies, {len(done)} done, {len(todo)} to go", flush=True)
    wlock = threading.Lock()
    count = [0]

    def work(r):
        try:
            res = process(r)
        except Exception as e:  # leave it for the next resume
            print("ERR", r["company_id"], e, flush=True)
            return
        with wlock:
            with open(OUT, "a") as f:
                f.write(json.dumps(res) + "\n")
            count[0] += 1
            if count[0] % 25 == 0:
                print(f"{count[0]}/{len(todo)}", flush=True)

    with ThreadPoolExecutor(max_workers=int(sys.argv[1]) if len(sys.argv) > 1 else 4) as ex:
        list(ex.map(work, todo))
    print("done", flush=True)


if __name__ == "__main__":
    main()
