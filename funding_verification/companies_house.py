"""Tier 1 (non-US): UK Companies House pass for GB companies.

Searches officers by each founder's name, keeps appointments at companies whose
name matches the brand (or were incorporated within two years of the batch),
then lists share allotment filings (SH01) from the company's filing history.
Writes one JSON line per company to companies_house.jsonl.
"""
import csv
import html
import json
import os
import re
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "companies_house.jsonl")
BASE = "https://find-and-update.company-information.service.gov.uk"
UA = "Mozilla/5.0 (research; sanjay@fastcode.ai)"


def get(path):
    time.sleep(0.6)
    req = urllib.request.Request(BASE + path, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8", "replace")
    except Exception:
        return ""


def text(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def core(s):
    stop = {"ltd", "limited", "inc", "the", "ai", "labs", "technologies", "technology", "uk", "hq", "group", "holdings"}
    return [w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in stop]


def officer_companies(name):
    page = get("/search/officers?q=" + urllib.parse.quote(name))
    out = []
    for m in re.finditer(r'<a[^>]+href="(/officers/[^"/]+/appointments)"[^>]*>(.*?)</a>', page, re.S):
        if core(text(m.group(2))) and set(core(name)) <= set(core(text(m.group(2)).replace(",", " "))):
            ap = get(m.group(1))
            for c in re.finditer(r'href="/company/([A-Z0-9]{8})"[^>]*>(.*?)</a>', ap, re.S):
                out.append((c.group(1), text(c.group(2))))
    return list(dict.fromkeys(out))


def filings(num):
    page = get(f"/company/{num}/filing-history?category=capital")
    rows = []
    for tr in re.findall(r"<tr>(.*?)</tr>", page, re.S):
        cells = [text(td) for td in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        doc = re.search(r'href="(/company/[^"]+/document\?format=pdf[^"]*)"', tr)
        if len(cells) >= 3:
            rows.append({"date": cells[0], "type": cells[1], "description": cells[2],
                         "pdf": BASE + html.unescape(doc.group(1)) if doc else ""})
    return rows


def main():
    rows = [r for r in csv.DictReader(open(os.path.join(HERE, "companies.csv"))) if r["country"] == "GB"]
    done = set()
    if os.path.exists(OUT):
        done = {json.loads(l)["company_id"] for l in open(OUT) if l.strip()}
    for r in rows:
        if r["company_id"] in done:
            continue
        brand = core(r["company"])
        cands = {}
        for f in [x.strip() for x in r["founders"].split(";") if x.strip()]:
            f = re.sub(r"\(.*?\)", " ", f)
            for num, name in officer_companies(f):
                if brand and core(name)[: len(brand)] == brand:
                    cands.setdefault(num, {"number": num, "name": name, "founders": []})["founders"].append(f.strip())
        for c in cands.values():
            c["capital_filings"] = filings(c["number"])
        res = {"company_id": r["company_id"], "company": r["company"], "uk_entities": list(cands.values()),
               "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        with open(OUT, "a") as fh:
            fh.write(json.dumps(res) + "\n")
        print(r["company"], [(c["name"], len(c["capital_filings"])) for c in cands.values()], flush=True)


if __name__ == "__main__":
    main()
