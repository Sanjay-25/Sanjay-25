"""Add latest-round, total-raised, cap and hiring fields to results.csv and write the qualifying list.

Inputs (this folder): companies.csv, results.csv (from ../build_results.py with FV_DIR set here),
edgar_pass.jsonl, web_pass.jsonl, hiring.jsonl, review/*_results.jsonl (optional).
Outputs: results_expanded.csv (every company) and qualified.csv (raised post-YC, total raised <= $200M).
"""
import csv
import glob
import json
import os
import re
import sys
from datetime import date

# Run on another folder (e.g. the first run's) by passing it as the first argument.
HERE = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
os.environ["FV_DIR"] = HERE
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import build_results as b  # noqa: E402

CAP = 200_000_000
# Manual decisions from the cap review (review/cap_batch*_results.jsonl).
FORMD_EXCLUDE = {"legion-health"}  # matched Form D belongs to a different company
CAP_OVERRIDE = {
    "legion-health": (8_300_000, "Yes"),
    "gilgamesh-pharmaceuticals": (None, "Unknown"),  # spinout raised $60M; original company ~$150M per aggregator only
    "tractian": (None, "Unknown"),  # "over $180M" (Crunchbase News); Forbes snippet says $200M; plus a debt facility
    "happyrobot": (None, "Unknown"),  # company says "around $200 million"
    # Only non-USD amounts known, all far below $200M.
    "bitstack": (None, "Yes"), "heycharge": (None, "Yes"), "invitris": (None, "Yes"),
    "revenir": (None, "Yes"), "autone": (None, "Yes"),
}
EXTRA = ["latest_round_date", "latest_round_amount_usd", "latest_round_source", "total_raised_known_usd",
         "within_200m_cap", "website", "industry", "yc_stage", "team_size", "yc_is_hiring", "yc_open_jobs",
         "ats", "ats_open_jobs", "open_positions", "eng_jobs", "ml_jobs", "careers_url", "job_titles_sample"]


def load(name):
    p = os.path.join(HERE, name)
    out = {}
    if os.path.exists(p):
        for line in open(p):
            if line.strip():
                d = json.loads(line)
                out[d["company_id"]] = d
    return out


def iso(s):
    s = (s or "").strip()
    if len(s) == 7:  # YYYY-MM
        s += "-01"
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
DATE_RX = [
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), lambda m: (int(m[1]), int(m[2]), int(m[3]))),
    (re.compile(r"\b(" + MONTHS + r")[a-z]*\.? (\d{1,2}),? (20\d{2})\b", re.I),
     lambda m: (int(m[3]), MONTHS.split("|").index(m[1].lower()[:4] if m[1].lower().startswith("sept") else m[1].lower()[:3]) + 1 - (1 if m[1].lower().startswith("sept") else 0), int(m[2]))),
    (re.compile(r"\b(\d{1,2}) (" + MONTHS + r")[a-z]* (20\d{2})\b", re.I),
     lambda m: (int(m[3]), MONTHS.split("|").index(m[2].lower()[:3]) + 1, int(m[1]))),
]


def date_near_quote(cid, quote, start):
    """Find a date printed within ~250 characters of the quote in the saved company pages."""
    key = " ".join(quote.split()[:6])
    best = None
    for p in glob.glob(os.path.join(HERE, "sources", "web", cid, "page*.txt")):
        text = open(p).read()
        i = text.find(key)
        if i < 0:
            continue
        window = text[max(0, i - 250): i + len(quote) + 250]
        for rx, conv in DATE_RX:
            for m in rx.finditer(window):
                try:
                    y, mo, d = conv(m)
                    dt = date(y, mo, d)
                except (ValueError, IndexError):
                    continue
                if start <= dt <= date.today():
                    dist = abs(m.start() - (i - max(0, i - 250)))
                    if best is None or dist < best[0]:
                        best = (dist, dt)
    return best[1] if best else None


def main():
    companies = {r["company_id"]: r for r in csv.DictReader(open(os.path.join(HERE, "companies.csv")))}
    results = list(csv.DictReader(open(os.path.join(HERE, "results.csv"))))
    edgar, web, hiring = load("edgar_pass.jsonl"), load("web_pass.jsonl"), load("hiring.jsonl")
    review = {}
    for p in glob.glob(os.path.join(HERE, "review", "*_results.jsonl")):
        for line in open(p):
            if line.strip():
                d = json.loads(line)
                review[d["company_id"]] = d
    for ed in edgar.values():
        for f in ed.get("matched", []):
            b.SHARED_ADSH.setdefault(f["adsh"], set()).add(ed["company_id"])

    out = []
    for r in results:
        cid, row = r["company_id"], companies[r["company_id"]]
        start = b.batch_start(row["batch"])
        rounds = []  # (date, amount or None, source)
        formd_total = 0.0
        ed = edgar.get(cid)
        if ed and cid not in FORMD_EXCLUDE:
            filings = [f for f in ed.get("matched", []) if b.plausible_issuer(f, row["company"], start)]
            offerings = {}
            for f in sorted(filings, key=lambda x: (x["file_date"], x["adsh"])):
                offerings[f["file_num"] or f["adsh"]] = f
            for f in offerings.values():
                lab, fs = b.classify_offering(f, start)
                if lab == "post_yc":
                    amt = b.num(f["total_amount_sold"]) or 0
                    formd_total += amt
                    rounds.append((fs, amt, f"SEC Form D {f['adsh']}"))
        if r["raised_post_yc"] == "Yes" and r["round_date"]:
            rounds.append((iso(r["round_date"]), b.num(r["post_yc_amount_usd"]), r["source_type"] or "result row"))
        if web:
            for c in b.funding_claims(row, web):
                d = None
                if c["date"]:
                    d = iso(b.claim_result(c)[0]["round_date"])
                if d and d >= start:
                    rounds.append((d, c["amount"] if c["currency"] == "$" else None, c["url"]))
        rv = review.get(cid)
        if rv and rv.get("found") and rv.get("post_yc") and iso(rv.get("round_date")):
            rounds.append((iso(rv["round_date"]), rv.get("amount_usd"), rv.get("source_url") or "web search review"))
        if r["raised_post_yc"] == "Yes" and not any(x[0] for x in rounds) and r["source_quote"]:
            d = date_near_quote(cid, r["source_quote"], start)
            if d:
                rounds.append((d, b.num(r["post_yc_amount_usd"]), (r["source_url"] or "company page") + " (date printed near the quote)"))
        dated = [x for x in rounds if x[0]]
        latest = max(dated, key=lambda x: x[0]) if dated else None

        amounts = [formd_total] + [x[1] for x in rounds if x[1]]
        if r["post_yc_amount_usd"]:
            amounts.append(float(r["post_yc_amount_usd"]))
        if rv and rv.get("total_raised_usd"):
            amounts.append(float(rv["total_raised_usd"]))
        total = max(amounts) if any(amounts) else None
        if r["raised_post_yc"] != "Yes":
            cap = ""
        elif rv and rv.get("over_200m") is True:
            cap = "No"
        elif total is None:
            cap = "Unknown"
        else:
            cap = "Yes" if total <= CAP else "No"

        if cid in CAP_OVERRIDE and r["raised_post_yc"] == "Yes":
            total, cap = CAP_OVERRIDE[cid][0] or total, CAP_OVERRIDE[cid][1]
            if CAP_OVERRIDE[cid][0]:
                total = CAP_OVERRIDE[cid][0]
        h = hiring.get(cid, {})
        yc_jobs = int(row.get("yc_open_jobs") or 0)
        ats_jobs = h.get("ats_open_jobs")
        e = dict(r)
        e.update({
            "latest_round_date": latest[0].isoformat() if latest else "",
            "latest_round_amount_usd": b.fmt_money(latest[1]) if latest and latest[1] else "",
            "latest_round_source": latest[2] if latest else "",
            "total_raised_known_usd": b.fmt_money(total) if total else "",
            "within_200m_cap": cap,
            "website": row["website"], "industry": row.get("industry", ""), "yc_stage": row.get("yc_stage", ""),
            "team_size": row.get("team_size", ""), "yc_is_hiring": row.get("yc_is_hiring", ""), "yc_open_jobs": yc_jobs,
            "ats": h.get("ats", ""), "ats_open_jobs": "" if ats_jobs is None else ats_jobs,
            "open_positions": max(yc_jobs, ats_jobs or 0),
            "eng_jobs": h.get("eng_jobs", ""), "ml_jobs": h.get("ml_jobs", ""),
            "careers_url": h.get("careers_url", ""),
            "job_titles_sample": "; ".join((h.get("titles") or row.get("yc_job_titles", "").split("; "))[:10]),
        })
        out.append(e)

    cols = list(results[0].keys()) + EXTRA
    with open(os.path.join(HERE, "results_expanded.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(out)
    q = [e for e in out if e["raised_post_yc"] == "Yes" and e["within_200m_cap"] in ("Yes", "Unknown")]
    with open(os.path.join(HERE, "qualified.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(q)
    print(f"{len(out)} companies; {len(q)} qualified (raised post-YC, total <= $200M or unknown); "
          f"{sum(e['within_200m_cap'] == 'No' for e in out)} over cap")


if __name__ == "__main__":
    main()
