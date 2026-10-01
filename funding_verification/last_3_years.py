"""Combine every batch group into one list of YC companies that raised since 1 Oct 2023.

Inputs: results_expanded.csv in this folder (Fall 2024 to Summer 2026), batch_2020_2024/ and batch_pre2020/,
plus the date checks in batch_pre2020/review/date_batch*_results.jsonl.
Output: last_3_years.csv.
"""
import csv
import glob
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CUTOFF = "2023-10-01"
GROUPS = [
    ("2005 to Summer 2019", os.path.join(HERE, "batch_pre2020")),
    ("Winter 2020 to Summer 2024", os.path.join(HERE, "batch_2020_2024")),
    ("Fall 2024 to Summer 2026", HERE),
]
COLS = ["batch_group", "company_id", "company", "batch", "website", "one_liner", "latest_round_date",
        "latest_round_amount_usd", "round_type", "lead_investors", "total_raised_known_usd", "over_200m",
        "date_basis", "source_tier", "confidence", "source_url", "source_quote", "team_size", "yc_is_hiring",
        "open_positions", "eng_jobs", "ml_jobs", "ats", "careers_url", "job_titles_sample", "industry", "notes"]


def norm_date(s):
    s = (s or "").strip()
    return s + "-01" if len(s) == 7 else s[:10]


def main():
    dates = {}
    for p in glob.glob(os.path.join(HERE, "batch_pre2020", "review", "date_batch*_results.jsonl")):
        for line in open(p):
            if line.strip():
                d = json.loads(line)
                dates[d["company_id"]] = d
    out, seen = [], set()
    for label, folder in GROUPS:
        comp = {r["company_id"]: r for r in csv.DictReader(open(os.path.join(folder, "companies.csv")))}
        for r in csv.DictReader(open(os.path.join(folder, "results_expanded.csv"))):
            if r["raised_post_yc"] != "Yes" or r["company_id"] in seen:
                continue
            date, amount, basis = r["latest_round_date"], r["latest_round_amount_usd"], r["latest_round_source"]
            rtype, lead, src, quote, tier = r["round_type"], r["lead_investors"], r["source_url"], r["source_quote"], r["source_tier"]
            notes = r["notes"]
            total, over = r["total_raised_known_usd"], {"No": "Yes", "Yes": "No"}.get(r["within_200m_cap"], "Unknown")
            d = dates.get(r["company_id"])
            if d and d.get("found") and norm_date(d.get("round_date")) > (date or ""):
                date, amount = norm_date(d["round_date"]), d.get("amount_usd") or ""
                rtype, lead = d.get("round_type") or "", d.get("lead_investors") or ""
                src, quote, tier = d.get("source_url") or src, d.get("source_quote") or quote, str(d.get("source_tier") or tier)
                basis = "web search date check"
                if d.get("total_raised_usd"):
                    total = d["total_raised_usd"]
                if d.get("over_200m") is not None:
                    over = "Yes" if d["over_200m"] else "No"
                notes = ("Date check: " + (d.get("notes") or "")[:300] + " " + notes)[:1500]
            # Batches from Fall 2024 on started after the cutoff, so any post-YC raise is inside the window.
            recent_batch = label.startswith("Fall 2024")
            if not (recent_batch or (date and date >= CUTOFF)):
                continue
            seen.add(r["company_id"])
            c = comp.get(r["company_id"], {})
            out.append({
                "batch_group": label, "company_id": r["company_id"], "company": r["company"], "batch": r["batch"],
                "website": r["website"], "one_liner": c.get("one_liner", "").strip(),
                "latest_round_date": date, "latest_round_amount_usd": amount,
                "round_type": rtype.replace(" (per Form D; stage not stated)", "").split(" (")[0],
                "lead_investors": lead, "total_raised_known_usd": total, "over_200m": over,
                "date_basis": basis if date else "batch started after the cutoff; round undated",
                "source_tier": tier, "confidence": r["confidence"], "source_url": src, "source_quote": quote,
                "team_size": r["team_size"], "yc_is_hiring": r["yc_is_hiring"], "open_positions": r["open_positions"],
                "eng_jobs": r["eng_jobs"], "ml_jobs": r["ml_jobs"], "ats": r["ats"], "careers_url": r["careers_url"],
                "job_titles_sample": r["job_titles_sample"], "industry": r["industry"], "notes": notes,
            })
    out.sort(key=lambda x: x["latest_round_date"] or "", reverse=True)
    with open(os.path.join(HERE, "last_3_years.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(out)
    by = {}
    for x in out:
        by[x["batch_group"]] = by.get(x["batch_group"], 0) + 1
    print(len(out), "companies raised since", CUTOFF, by)


if __name__ == "__main__":
    main()
