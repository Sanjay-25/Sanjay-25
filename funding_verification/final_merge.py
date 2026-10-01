"""Merge our verified five-year list with Tracxn's into the final list.

Criteria: YC company (went through a YC batch), active, raised beyond YC's own investment on or after
1 Oct 2021, total funding at or under $200M.

Inputs: last_5_years.csv (ours), tracxn/tracxn_yc_5y_full.csv (Tracxn: YC-backed, a round over $600K since
Oct 2021, total equity funding <= $200M, not acquired/dead/public), tracxn/yc_all_directory.json (YC status and
batch), tracxn/unmatched_yc_batch.json, and the results_expanded.csv files for hiring signals.
Outputs: final_5y_under_200m.csv and final_excluded.csv.
"""
import csv
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
CAP = 200_000_000


def dom(u):
    u = re.sub(r"^https?://", "", (u or "").lower().strip()).split("/")[0].split("?")[0].split("#")[0]
    return u[4:] if u.startswith("www.") else u


def norm(n):
    return re.sub(r"[^a-z0-9]", "", (n or "").lower())


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main():
    yc = json.load(open(os.path.join(HERE, "tracxn", "yc_all_directory.json")))
    ycd, ycn = {}, {}
    for h in yc:
        if dom(h["website"]):
            ycd.setdefault(dom(h["website"]), h)
        ycn.setdefault(norm(h["name"]), h)
        for fn in h.get("former_names") or []:
            ycn.setdefault(norm(fn), h)
    unmatched = json.load(open(os.path.join(HERE, "tracxn", "unmatched_yc_batch.json")))

    # Hiring and metadata for every company we processed.
    meta = {}
    for folder in ["", "batch_2020_2024", "batch_pre2020"]:
        p = os.path.join(HERE, folder, "results_expanded.csv")
        for r in csv.DictReader(open(p)):
            meta[dom(r["website"])] = r
            meta.setdefault("name:" + norm(r["company"]), r)

    ours = list(csv.DictReader(open(os.path.join(HERE, "last_5_years.csv"))))
    by_dom = {dom(r["website"]): r for r in ours}
    by_name = {norm(r["company"]): r for r in ours}

    out, excluded, used = [], [], set()
    for t in csv.DictReader(open(os.path.join(HERE, "tracxn", "tracxn_yc_5y_full.csv"))):
        h = ycd.get(t["domain"]) or ycd.get(dom(t["website"])) or ycn.get(norm(t["company"]))
        o = by_dom.get(t["domain"]) or by_dom.get(dom(t["website"])) or by_name.get(norm(t["company"]))
        if o:
            used.add(o["company_id"])
        reason = ""
        if not h and not o:
            u = unmatched.get(t["tracxn_id"], {})
            if not u.get("yc_batch"):
                reason = "YC fund investment, no YC batch on record"
        elif h and h["status"] != "Active" and not o:
            reason = f"YC lists the company as {h['status']}"
        if o and o.get("over_200m") == "Yes":
            reason = "Our verified total is over $200M (Tracxn equity total is lower)"
        row = build(t, o, h, meta, unmatched)
        if reason:
            row["exclusion_reason"] = reason
            excluded.append(row)
        else:
            out.append(row)
    for o in ours:
        if o["company_id"] in used:
            continue
        row = build(None, o, ycd.get(dom(o["website"])), meta, unmatched)
        if o.get("over_200m") == "Yes":
            row["exclusion_reason"] = "Total raised over $200M"
            excluded.append(row)
        else:
            out.append(row)
    out.sort(key=lambda r: r["latest_round_date"] or "", reverse=True)
    cols = list(out[0].keys())
    for name, rows in [("final_5y_under_200m.csv", out), ("final_excluded.csv", excluded)]:
        with open(os.path.join(HERE, name), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols + (["exclusion_reason"] if name == "final_excluded.csv" else []),
                               extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
    src = {}
    for r in out:
        src[r["source"]] = src.get(r["source"], 0) + 1
    print(len(out), "final;", len(excluded), "excluded;", src)


def build(t, o, h, meta, unmatched):
    site = (o or {}).get("website") or (t or {}).get("website") or (h or {}).get("website") or ""
    m = meta.get(dom(site)) or meta.get("name:" + norm((o or t or {}).get("company"))) or {}
    t_date, o_date = (t or {}).get("latest_round_date", ""), (o or {}).get("latest_round_date", "")
    use_t = bool(t) and (t_date > o_date)
    if use_t:
        date, amount, rtype = t_date, t.get("latest_round_amount_usd", ""), t.get("latest_round_name", "")
        basis = "Tracxn"
    else:
        date, amount, rtype = o_date, (o or {}).get("latest_round_amount_usd", ""), (o or {}).get("round_type", "")
        basis = "Verified (" + ((o or {}).get("date_basis") or "source") + ")"
    total = (t or {}).get("total_equity_funding_usd") or (o or {}).get("total_raised_known_usd", "")
    batch = (h or {}).get("batch") or (o or {}).get("batch") or unmatched.get((t or {}).get("tracxn_id", ""), {}).get("yc_batch", "")
    return {
        "company": (o or {}).get("company") or (h or {}).get("name") or (t or {}).get("company"),
        "yc_batch": batch,
        "yc_status": (h or {}).get("status", "") or ("Active" if o else ""),
        "website": site,
        "one_liner": ((o or {}).get("one_liner") or (h or {}).get("one_liner") or "").strip(),
        "industry": (h or {}).get("industry") or (o or {}).get("industry", ""),
        "location": (h or {}).get("all_locations", ""),
        "latest_round_date": date,
        "latest_round_amount_usd": amount,
        "latest_round_type": rtype,
        "latest_round_basis": basis,
        "total_funding_usd": total,
        "lead_investors": (o or {}).get("lead_investors", "") if not use_t else "",
        "source": "Both" if (t and o) else ("Tracxn only" if t else "Verified only"),
        "verified_source_url": (o or {}).get("source_url", ""),
        "team_size": m.get("team_size") or (h or {}).get("team_size", ""),
        "yc_is_hiring": m.get("yc_is_hiring") or ("Yes" if (h or {}).get("isHiring") else ""),
        "open_positions": m.get("open_positions", ""),
        "eng_jobs": m.get("eng_jobs", ""),
        "ml_jobs": m.get("ml_jobs", ""),
        "careers_url": m.get("careers_url", ""),
        "job_titles_sample": m.get("job_titles_sample", ""),
        "tracxn_id": (t or {}).get("tracxn_id", ""),
    }


if __name__ == "__main__":
    main()
