"""Print the end-of-run summary for results.csv (counts and the Unclear list)."""
import collections
import csv
import os

HERE = os.path.dirname(os.path.abspath(__file__))
rows = list(csv.DictReader(open(os.path.join(HERE, "results.csv"))))
print(f"Companies: {len(rows)}\n")
print("By raised_post_yc:")
for k, v in collections.Counter(r["raised_post_yc"] for r in rows).most_common():
    print(f"  {k:18} {v}")
print("\nBy source_tier:")
for k, v in sorted(collections.Counter(r["source_tier"] or "none" for r in rows).items()):
    print(f"  {k:18} {v}")
print("\nYes by source_tier / confidence:")
for k, v in sorted(collections.Counter((r["source_tier"], r["confidence"]) for r in rows
                                      if r["raised_post_yc"] == "Yes").items()):
    print(f"  tier {k[0]} {k[1]:8} {v}")
unclear = [r for r in rows if r["raised_post_yc"] == "Unclear"]
print(f"\nStill Unclear ({len(unclear)}):")
for r in unclear:
    print(f"  {r['company']} ({r['batch']}): {r['notes'][:110]}")
