"""Merge the passes into results.csv following the source hierarchy in CLAUDE.md.

Inputs: companies.csv, edgar_pass.jsonl (tier 1), companies_house.jsonl (tier 1,
GB), web_pass.jsonl (tier 2 site/YC page, tier 4 press via YC news links),
overrides.csv (manual review decisions, applied last).

Appends one row per company to results.csv and skips companies already in it,
so it can resume. Pass --rebuild to start results.csv from scratch.
"""
import csv
import json
import os
import re
import sys
import urllib.parse
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
COLS = open(os.path.join(HERE, "output_template.csv")).readline().strip().split(",")
YC_DEAL = 500_000
BATCH_MONTH = {"Winter": 1, "Spring": 4, "Summer": 6, "Fall": 9}
FUNDISH = re.compile(r"\b(fund|funds|lp|l\.p\.|spv|capital|ventures|partners|reit|a series of|investments?|holdings llc)\b", re.I)


def load_jsonl(name):
    p = os.path.join(HERE, name)
    out = {}
    if os.path.exists(p):
        for line in open(p):
            if line.strip():
                d = json.loads(line)
                out[d["company_id"]] = d
    return out


def batch_start(batch):
    season, year = batch.split()
    return date(int(year), BATCH_MONTH[season], 1)


def to_date(s):
    try:
        return date.fromisoformat(s[:10])
    except (TypeError, ValueError):
        return None


def num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def fmt_money(x):
    return str(int(round(x))) if x is not None else ""


def words(s):
    return re.findall(r"[a-z0-9]+", s.lower())


# ---------------------------------------------------------------- tier 1: EDGAR

def classify_offering(f, start):
    """Return (label, first_sale_date) for the latest filing of one offering."""
    fs = to_date(f["date_first_sale"]) or to_date(f["file_date"])
    sold = num(f["total_amount_sold"]) or 0
    if fs is None:
        return "unknown", fs
    if fs < start - timedelta(days=45):
        return "pre_yc", fs
    if sold <= 600_000 and fs <= start + timedelta(days=150):
        return "yc_deal_like", fs
    if sold < 100_000:
        return "unsold" if sold == 0 else "small", fs
    return "post_yc", fs


BANKISH = re.compile(r"\b(bancorp|bancshares|bank|financial|credit union|trust|reit|properties|realty|homes|"
                     r"therapeutics|biosciences|pharma\w*|energy|mining|metals|gold|minerals)\b", re.I)
NEW_CIK = 1_980_000  # CIKs assigned from about early 2024 onwards
SHARED_ADSH = {}  # accession -> company_ids it matched on founder names (filled in main)


def brand_matches(f, company):
    stop = {"inc", "ai", "labs", "the", "co", "hq", "technologies", "formerly"}
    brand = [w for w in words(company) if w not in stop]
    issuer = [w for w in words(f["entity_name"]) if w not in stop]
    return bool(brand) and (any(w in issuer for w in brand) or "".join(brand) in "".join(issuer))


def plausible_issuer(f, company, start):
    """Is this Form D from the company itself, not a same-named person's other entity?"""
    if brand_matches(f, company):
        return True
    name = f["entity_name"]
    if FUNDISH.search(name) or BANKISH.search(name) or re.search(r"\b(llc|l\.l\.c|lp|l\.p)\b\.?$", name, re.I):
        return False
    if len(SHARED_ADSH.get(f["adsh"], ())) > 1:
        return False
    fs = to_date(f["date_first_sale"]) or to_date(f["file_date"])
    if fs and fs < start - timedelta(days=365):
        return False
    # Different legal name: need two listed founders on the filing and a recently assigned CIK.
    return len(f["matched_founders"]) >= 2 and int(f["cik"] or 0) >= NEW_CIK


def weak_candidates(ed, company, start):
    """Single-founder, different-name filings: reported in notes only."""
    out = []
    for f in ed.get("matched", []):
        if plausible_issuer(f, company, start) or brand_matches(f, company):
            continue
        name = f["entity_name"]
        if FUNDISH.search(name) or BANKISH.search(name) or len(SHARED_ADSH.get(f["adsh"], ())) > 1:
            continue
        fs = to_date(f["date_first_sale"]) or to_date(f["file_date"])
        if int(f["cik"] or 0) >= NEW_CIK and fs and fs >= start - timedelta(days=45):
            out.append(f"Unverified Form D by same-named person: {name} {f['adsh']} sold {f['total_amount_sold']} "
                       f"(first sale {f['date_first_sale']}, {f['city'].title()}); likely a different person.")
    return out


def edgar_result(row, ed):
    start = batch_start(row["batch"])
    filings = [f for f in ed.get("matched", []) if plausible_issuer(f, row["company"], start)]
    weak = weak_candidates(ed, row["company"], start)
    if not filings:
        return None, weak
    # Latest filing per offering (D/A share the original's SEC file number).
    offerings = {}
    for f in sorted(filings, key=lambda x: (x["file_date"], x["adsh"])):
        offerings[f["file_num"] or f["adsh"]] = f
    labelled = [(classify_offering(f, start), f) for f in offerings.values()]
    post = [(fs, f) for (lab, fs), f in labelled if lab == "post_yc"]
    notes = []
    for (lab, fs), f in labelled:
        if lab != "post_yc":
            notes.append(f"{lab.replace('_', ' ')} Form D {f['adsh']}: sold {f['total_amount_sold'] or '?'} "
                         f"first sale {f['date_first_sale']}")
    notes += weak
    if not post:
        return {"_edgar_only": True, "legal_name": filings[-1]["entity_name"], "sec_cik": filings[-1]["cik"]}, notes
    post.sort(key=lambda x: x[0])
    amount = sum(num(f["total_amount_sold"]) or 0 for _, f in post)
    main_fs, main = max(post, key=lambda x: num(x[1]["total_amount_sold"]) or 0)
    sec = "; ".join(main["security_types"])
    if len(post) > 1:
        notes.insert(0, f"{len(post)} post-YC Form D offerings summed: " +
                     ", ".join(f"{f['adsh']} ${f['total_amount_sold']} ({f['date_first_sale']})" for _, f in post))
    if main_fs <= start + timedelta(days=60):
        notes.append("First sale is close to batch start, so the Form D total may include YC's $500K.")
    conf = "High"
    if not brand_matches(main, row["company"]):
        notes.insert(0, f"Legal name differs from brand; matched on related person(s) {', '.join(main['matched_founders'])}"
                        f", issuer in {main['city'].title()}, {main['state']}.")
        if len(main["matched_founders"]) < 2:
            conf = "Medium"
            notes.insert(1, "Identity rests on one founder name; verify.")
    if main["clarification"]:
        notes.append("Form D clarification: " + main["clarification"][:200])
    rtype = "SAFE" if re.search(r"safe|simple agreement", sec, re.I) else \
        "Convertible note" if "Debt" in sec else "Equity" if "Equity" in sec else sec
    cik = str(int(main["cik"]))
    return {
        "raised_post_yc": "Yes",
        "post_yc_amount_usd": fmt_money(amount),
        "round_type": f"{rtype} (per Form D; stage not stated)",
        "round_date": post[0][0].isoformat(),
        "source_tier": "1",
        "source_type": "SEC Form D" + ("/A" if main["is_amendment"] else ""),
        "source_url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{main['adsh'].replace('-', '')}/primary_doc.xml",
        "source_quote": f"totalAmountSold: {main['total_amount_sold']}; totalOfferingAmount: "
                        f"{main['total_offering_amount']}; dateOfFirstSale: {main['date_first_sale']}",
        "sec_cik": main["cik"],
        "sec_accession": main["adsh"],
        "sec_total_amount_sold": main["total_amount_sold"],
        "sec_total_offering_amount": main["total_offering_amount"],
        "sec_date_first_sale": main["date_first_sale"],
        "sec_security_type": sec,
        "sec_investor_count": main["investor_count"],
        "legal_name": main["entity_name"],
        "confidence": conf,
    }, notes


# ------------------------------------------------------- tier 2 / 4: web + press

MONEY_RE = re.compile(r"(US\s?)?([$€£])\s?(\d[\d,]*(?:\.\d+)?)\s?(k|m|mm|b|bn|million|billion|thousand)?\b", re.I)
RAISE_RE = re.compile(r"\b(raised|raises|closed|closes|secured|secures|lands|landed|nabs|bags|"
                      r"(?:seed|pre-seed|series [a-c]|funding|financing) round|in (?:seed|pre-seed|series [a-c] )?funding)\b", re.I)
NOISE_RE = re.compile(r"\b(customers?|clients?|helped|saved|save|revenue|arr|gmv|processed|portfolio companies|"
                      r"our users|per month|/mo|per year|pricing|price|plan|salary|compensation|equity range|"
                      r"loans?|invoices?|recovered|claims?|founders we|companies that|startups that|lois?|letters? of intent|"
                      r"enterprise value|deal value|contracts?|pipeline|exited|exits|previously|prior|collectively|"
                      r"combined|more than|over \$|track record|alumni|built and|backed companies|grants?)\b", re.I)
LED_RE = re.compile(r"\b(?:led by|co-led by)\s+([A-Z][\w&.'\-]*(?:\s+(?:[A-Z][\w&.'\-]*|&|and|of))*)")
STAGE_RE = re.compile(r"\b(pre-seed|seed|series [a-d]|bridge)\b", re.I)


def parse_money(sent):
    best = None
    for m in MONEY_RE.finditer(sent):
        cur, val, unit = m.group(2), float(m.group(3).replace(",", "")), (m.group(4) or "").lower()
        mult = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mm": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9}.get(unit, 1)
        v = val * mult
        after = sent[m.end():m.end() + 25].lower()
        before = sent[max(0, m.start() - 20):m.start()].lower()
        if "valuation" in after or "valued at" in before or "cap" in after.split()[:2]:
            continue
        if v < 50_000 or v > 5e9:
            continue
        if best is None or v > best[1]:
            best = (cur, v, m.group(0))
    return best


def funding_claims(row, web):
    """Snippets that state this company raised a specific amount."""
    brand = row["company"].lower().split(",")[0].replace(" inc", "").strip()
    domain = (row["domain"] or "").lower().removeprefix("www.")
    claims = []
    for s in web.get("snippets", []):
        t = s["text"]
        if not RAISE_RE.search(t) or NOISE_RE.search(t):
            continue
        money = parse_money(t)
        if not money:
            continue
        low = t.lower()
        about_us = brand in low or re.search(r"\b(we|we've|we have|our)\b", low) or s["kind"] == "yc_news_title"
        if not about_us:
            continue
        if money[1] <= 600_000 and ("y combinator" in low or "yc" in words(low)):
            continue  # YC's own standard deal
        host = urllib.parse.urlparse(s["url"] or "").netloc.lower().removeprefix("www.")
        own = bool(domain) and (host == domain or host.endswith("." + domain))
        if s["kind"] == "yc_page":
            tier, stype = 2, "YC company/launch page"
        elif own:
            tier, stype = 2, "Company website"
        elif host.endswith("ycombinator.com") and "launches" in (s["url"] or ""):
            tier, stype = 2, "YC launch page"
        elif host in {"linkedin.com", "x.com", "twitter.com"}:
            tier, stype = 2, "Company/founder social post"
        elif host in {"crunchbase.com", "pitchbook.com", "tracxn.com", "dealroom.co", "cbinsights.com", "wellfound.com"}:
            tier, stype = 5, "Aggregator"
        else:
            tier, stype = 4, "Press (linked from YC company page)"
        led = LED_RE.search(t)
        stage = STAGE_RE.search(t)
        claims.append({"tier": tier, "stype": stype, "url": s["url"], "text": t, "currency": money[0],
                       "amount": money[1], "money_text": money[2], "date": s.get("date", ""),
                       "lead": led.group(1).strip() if led else "", "stage": stage.group(1).title() if stage else ""})
    claims.sort(key=lambda c: (c["tier"], -c["amount"]))
    return claims


def short_quote(text, anchor):
    ws = text.split()
    idx = next((i for i, w in enumerate(ws) if anchor.split()[0] in w), 0)
    lo = max(0, idx - 10)
    return " ".join(ws[lo:lo + 24])


def claim_result(c):
    usd = c["currency"] == "$"
    d = ""
    if c["date"]:
        for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d"):
            try:
                from datetime import datetime
                d = datetime.strptime(c["date"], fmt).date().isoformat()
                break
            except ValueError:
                pass
    return {
        "raised_post_yc": "Yes",
        "post_yc_amount_usd": fmt_money(c["amount"]) if usd else "",
        "round_type": c["stage"],
        "round_date": d,
        "lead_investors": c["lead"],
        "source_tier": str(c["tier"]),
        "source_type": c["stype"],
        "source_url": c["url"],
        "source_quote": short_quote(c["text"], c["money_text"]),
        "confidence": "High" if c["tier"] == 2 else "Medium" if c["tier"] in (3, 4) else "Low",
    }, ([] if usd else [f"Amount stated as {c['money_text']} (not converted to USD)."]) + \
        ([] if d else ["Round date not stated on the source page; assumed post-YC because it is presented as current news."])


# -------------------------------------------------------------------- main

def build_row(row, ed, ch, web, tracxn):
    out = {k: "" for k in COLS}
    out.update(company_id=row["company_id"], company=row["company"], batch=row["batch"])
    notes = []
    t1, t1notes = edgar_result(row, ed) if ed else (None, [])
    notes += t1notes
    claims = funding_claims(row, web) if web else []
    if t1 and not t1.get("_edgar_only"):
        out.update(t1)
        # Keep lower-tier framing (investors, stage) if we have it.
        for c in claims:
            if c["lead"] and not out["lead_investors"]:
                out["lead_investors"] = c["lead"]
            if c["currency"] == "$" and abs(c["amount"] - float(out["post_yc_amount_usd"] or 0)) > 0.25 * c["amount"]:
                notes.append(f"Tier {c['tier']} source states {c['money_text']} ({c['url']}); Form D figure kept.")
                break
        if claims and claims[0]["stage"]:
            out["round_type"] = f"{claims[0]['stage']} ({out['round_type'].split(' (')[0]} per Form D)"
    else:
        if t1 and t1.get("_edgar_only"):
            out["legal_name"], out["sec_cik"] = t1["legal_name"], t1["sec_cik"]
        start = batch_start(row["batch"])
        pre = [c for c in claims if claim_result(c)[0]["round_date"] and
               to_date(claim_result(c)[0]["round_date"]) < start - timedelta(days=45)]
        for c in pre:
            notes.append(f"Pre-YC raise, not counted: {c['money_text']} dated {claim_result(c)[0]['round_date']} ({c['url']}).")
        claims = [c for c in claims if c not in pre]
        if claims:
            res, extra = claim_result(claims[0])
            out.update(res)
            notes += extra
            if len(claims) > 1 and claims[1]["amount"] != claims[0]["amount"] and claims[1]["tier"] <= 4:
                notes.append(f"Other stated figure: {claims[1]['money_text']} ({claims[1]['url']}).")
            notes.append("No matching post-YC Form D found." if ed else "EDGAR not checked.")
        else:
            out["raised_post_yc"] = "No evidence found"
            bits = []
            if ed is not None:
                bits.append("no post-YC Form D matching the founders")
            if web is not None:
                bits.append(f"no funding statement on website ({web.get('site_pages_fetched', 0)} pages) or YC page")
            notes.append("Checked: " + "; ".join(bits) + ". LinkedIn/X/Crunchbase need a logged-in browser.")
            if tracxn:
                out["raised_post_yc"] = "Unclear"
                out["source_tier"] = "5"
                out["source_type"] = "Tracxn (lead only, from input)"
                out["confidence"] = "Low"
                notes.insert(0, f"Tracxn lead total ${int(tracxn):,}; not confirmed by a higher-tier source.")
    if ch:
        for e in ch.get("uk_entities", []):
            sh01 = [f for f in e.get("capital_filings", []) if f["type"].upper().startswith("SH01")]
            if sh01:
                notes.append(f"Companies House {e['name']}: {len(sh01)} SH01 allotment filing(s), latest "
                             f"{sh01[0]['date']}: {sh01[0]['description'][:120]}")
            elif e:
                notes.append(f"Companies House {e['name']}: no SH01 allotments listed.")
    if row["country"] and row["country"] not in ("US", "GB") and out["raised_post_yc"] != "Yes":
        notes.append(f"National register for {row['country']} not checked.")
    out["notes"] = " ".join(notes)[:1500]
    out["checked_at"] = max([x.get("checked_at", "") for x in (ed or {}, web or {}, ch or {})])
    return out


def apply_review(rows):
    """Web-search review of Tracxn-only leads (review/*_results.jsonl), applied before manual overrides."""
    import glob
    found = {}
    for p in sorted(glob.glob(os.path.join(HERE, "review", "*_results.jsonl"))):
        for line in open(p):
            if line.strip():
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                found[d["company_id"]] = d
    for r in rows:
        d = found.get(r["company_id"])
        if not d or r["source_tier"] not in ("5", ""):
            continue
        tier = str(d.get("source_tier") or "")
        note = "Web search review: " + (d.get("notes") or "")[:400]
        if d.get("found") and d.get("post_yc") and tier in ("2", "3", "4"):
            amt = d.get("amount_usd")
            r.update(raised_post_yc="Yes", post_yc_amount_usd=fmt_money(float(amt)) if amt else "",
                     round_type=d.get("round_type") or "", round_date=d.get("round_date") or "",
                     lead_investors=d.get("lead_investors") or "", source_tier=tier,
                     source_type=d.get("source_type") or "", source_url=d.get("source_url") or "",
                     source_quote=(d.get("source_quote") or "")[:200],
                     confidence="High" if tier == "2" and amt else "Medium" if amt else "Low")
            r["notes"] = (note + " " + r["notes"])[:1500]
        elif d.get("found") and d.get("post_yc") is False:
            r["notes"] = ("Only a pre-YC round found in web search: " + (d.get("source_url") or "") + ". "
                          + note + " " + r["notes"])[:1500]
        else:
            r["notes"] = (note + " " + r["notes"])[:1500]
    return rows


def apply_overrides(rows):
    p = os.path.join(HERE, "overrides.csv")
    if not os.path.exists(p):
        return rows
    by_id = {r["company_id"]: r for r in rows}
    for o in csv.DictReader(open(p)):
        r = by_id.get(o["company_id"])
        if r is None:
            continue
        for k, v in o.items():
            if k != "company_id" and v != "":
                r[k] = "" if v == "-" else v
    return rows


def main():
    rebuild = "--rebuild" in sys.argv
    companies = {}
    for r in csv.DictReader(open(os.path.join(HERE, "companies.csv"))):
        prev = companies.get(r["company_id"])
        if prev and not prev["tracxn_total_usd_LEAD_ONLY"]:
            prev["tracxn_total_usd_LEAD_ONLY"] = r["tracxn_total_usd_LEAD_ONLY"]
        companies.setdefault(r["company_id"], r)
    edgar, web, ch = load_jsonl("edgar_pass.jsonl"), load_jsonl("web_pass.jsonl"), load_jsonl("companies_house.jsonl")
    for cid, ed in edgar.items():
        for f in ed.get("matched", []):
            SHARED_ADSH.setdefault(f["adsh"], set()).add(cid)
    out_path = os.path.join(HERE, "results.csv")
    if rebuild and os.path.exists(out_path):
        os.remove(out_path)
    have = set()
    if os.path.exists(out_path):
        have = {r["company_id"] for r in csv.DictReader(open(out_path))}
    new = not os.path.exists(out_path)
    with open(out_path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        if new:
            w.writeheader()
        for cid, row in companies.items():
            if cid in have or cid not in edgar or cid not in web:
                continue
            res = apply_overrides(apply_review([build_row(row, edgar.get(cid), ch.get(cid), web.get(cid),
                                             num(row["tracxn_total_usd_LEAD_ONLY"]))]))[0]
            w.writerow(res)
            f.flush()


if __name__ == "__main__":
    main()
