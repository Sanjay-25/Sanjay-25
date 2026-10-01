"""Pull active YC companies for batches Winter 2020 to Summer 2024 from YC's public directory index."""
import json, urllib.request, urllib.parse, csv
APP = "45BWZJ1SGC"
KEY = ("NzJmMWExZWYxYzY5OGYwN2VkYWM5YzRiM2VlNDFlM2I0ODU2YjQ2Yjg0MTFiNWE5NzY0NTMyZGI1OWEwMzVjY2FuYWx5dGljc1RhZ3M9eWNkYyZy"
       "ZXN0cmljdEluZGljZXM9WUNDb21wYW55X3Byb2R1Y3Rpb24lMkNZQ0NvbXBhbnlfQnlfTGF1bmNoX0RhdGVfcHJvZHVjdGlvbiZ0YWdGaWx0ZXJzPSU1QiUyMnljZGNfcHVibGljJTIyJTVE")

def query(params):
    req = urllib.request.Request(f"https://{APP.lower()}-dsn.algolia.net/1/indexes/YCCompany_production/query",
                                 data=json.dumps({"params": urllib.parse.urlencode(params)}).encode(),
                                 headers={"X-Algolia-Application-Id": APP, "X-Algolia-API-Key": KEY,
                                          "Referer": "https://www.ycombinator.com/", "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=30))

facets = query({"hitsPerPage": 0, "facets": json.dumps(["batch"]), "maxValuesPerFacet": 200})["facets"]["batch"]
batches = sorted(b for b in facets if any(y in b for y in ("2020", "2021", "2022", "2023", "2024")) and b != "Fall 2024")
print({b: facets[b] for b in batches})
rows = []
for b in batches:
    page = 0
    while True:
        d = query({"hitsPerPage": 1000, "page": page, "facetFilters": json.dumps([f"batch:{b}", "status:Active"])})
        rows += d["hits"]
        page += 1
        if page >= d["nbPages"]:
            break
json.dump(rows, open("yc_active_2020_2024.json", "w"))
print(len(rows))
