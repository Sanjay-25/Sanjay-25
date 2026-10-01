"""Tier 2 pass: the company's own words (website pages + YC company/launch page).

For each company, fetches the YC company page (long description, launches and
the news items YC links), the homepage, common pages (/blog, /news, /press,
/about, /careers, /changelog, /security, /trust) and blog/news posts whose link
text or URL looks funding-related. Extracts sentences that mention a raise.

Checkpoints one JSON line per company to web_pass.jsonl; rerunning resumes.
Pages with funding snippets are saved as text under sources/web/<company_id>/.
"""
import csv
import html
import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# FV_DIR points a run at another working folder (companies.csv in, results out).
HERE = os.environ.get("FV_DIR") or os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "web_pass.jsonl")
SRC = os.path.join(HERE, "sources", "web")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
PATHS = ["", "/blog", "/news", "/press", "/about", "/company", "/careers", "/changelog", "/security", "/trust"]

MONEY = r"(?:US\s?)?[$€£]\s?\d[\d,]*(?:\.\d+)?\s?(?:k|m|mm|b|bn|million|billion|thousand)?\b|\b\d+(?:\.\d+)?\s?(?:million|m)\s(?:dollars|usd|eur|gbp)\b"
KEY = (r"\b(raised|raises|raise|raising|funding|funded|seed|pre-seed|series [a-d]|round|backed by|led by|"
       r"investment from|investors? include|participation from)\b")
LINK_KEY = re.compile(r"fund|rais|seed|series|invest|announc|backed", re.I)

_yc_lock = threading.Lock()
_yc_last = [0.0]


def fetch(url, timeout=20):
    if "ycombinator.com" in url:
        with _yc_lock:
            wait = _yc_last[0] + 0.4 - time.time()
            if wait > 0:
                time.sleep(wait)
            _yc_last[0] = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ctype = r.headers.get("Content-Type", "")
            if "html" not in ctype and "text" not in ctype and "json" not in ctype:
                return None, r.geturl()
            return r.read(1_500_000).decode("utf-8", "replace"), r.geturl()
    except Exception:
        return None, url


def to_text(s):
    s = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h\d|tr|section|article)>", ". ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def snippets(text):
    out = []
    for sent in re.split(r"(?<=[.!?])\s+", text):
        if len(sent) > 600:
            sent = sent[:600]
        if re.search(KEY, sent, re.I) and (re.search(MONEY, sent, re.I) or re.search(r"\b(led by|backed by)\b", sent, re.I)):
            out.append(sent.strip())
    return list(dict.fromkeys(out))


def links(page, base):
    res = []
    for m in re.finditer(r'(?is)<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', page):
        href, label = m.group(1), to_text(m.group(2))
        url = urllib.parse.urldefrag(urllib.parse.urljoin(base, href))[0]
        res.append((url, label))
    return res


def same_site(u, domain):
    host = urllib.parse.urlparse(u).netloc.lower()
    return host == domain or host.endswith("." + domain)


def yc_page(row):
    found = []
    cached = os.path.join(HERE, "yc_pages", row["company_id"] + ".html")
    if os.path.exists(cached):
        page = open(cached).read()
    else:
        page, _ = fetch(row["yc_page"]) if row["yc_page"] else (None, None)
    if not page:
        return found, []
    m = re.search(r'data-page="([^"]+)"', page)
    if not m:
        return found, []
    try:
        props = json.loads(html.unescape(m.group(1)))["props"]
    except Exception:
        return found, []
    c = props.get("company", {}) or {}
    news = [{"title": n.get("title"), "url": n.get("url"), "date": n.get("date")} for n in props.get("newsItems") or []]
    texts = [c.get("long_description") or ""]
    for L in props.get("launches") or []:
        texts.append(" ".join(str(L.get(k) or "") for k in ("title", "tagline", "body")))
    for s in snippets(to_text(" . ".join(texts))):
        found.append({"url": row["yc_page"], "kind": "yc_page", "text": s})
    for n in news:
        if n["title"] and re.search(KEY, n["title"], re.I):
            found.append({"url": n["url"], "kind": "yc_news_title", "text": n["title"], "date": n["date"]})
    return found, news


def process(row):
    found, news = yc_page(row)
    domain = (row["domain"] or "").lower().removeprefix("www.")
    site = row["website"].strip()
    if site and not site.startswith("http"):
        site = "https://" + site
    visited, pages_ok, saved = set(), 0, []
    queue = [site.rstrip("/") + p for p in PATHS] if site else []
    extra = []
    while queue:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        page, final = fetch(url)
        if not page or final in visited and final != url:
            continue
        visited.add(final)
        pages_ok += 1
        text = to_text(page)
        hits = snippets(text)
        for s in hits:
            found.append({"url": final, "kind": "site", "text": s})
        if hits:
            saved.append((final, text))
        # Follow funding-looking links on the same site (posts on blog/news index pages).
        for u, label in links(page, final):
            if domain and same_site(u, domain) and u not in visited and (LINK_KEY.search(label) or LINK_KEY.search(u)):
                if len(extra) < 6 and u not in extra:
                    extra.append(u)
                    queue.append(u)
    if saved:
        d = os.path.join(SRC, row["company_id"])
        os.makedirs(d, exist_ok=True)
        for i, (u, text) in enumerate(saved):
            with open(os.path.join(d, f"page{i}.txt"), "w") as f:
                f.write(u + "\n\n" + text[:200_000])
    return {
        "company_id": row["company_id"],
        "company": row["company"],
        "site_pages_fetched": pages_ok,
        "snippets": found,
        "yc_news": news,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def main():
    rows = list(csv.DictReader(open(os.path.join(HERE, "companies.csv"))))
    uniq = {}
    for r in rows:
        uniq.setdefault(r["company_id"], r)
    only = None
    if len(sys.argv) > 2:
        only = set(open(sys.argv[2]).read().split())
    done = set()
    if os.path.exists(OUT):
        done = {json.loads(l)["company_id"] for l in open(OUT) if l.strip()}
    todo = [r for k, r in uniq.items() if k not in done and (only is None or k in only)]
    print(f"{len(todo)} to go", flush=True)
    lock, n = threading.Lock(), [0]

    def work(r):
        try:
            res = process(r)
        except Exception as e:
            print("ERR", r["company_id"], e, flush=True)
            return
        with lock:
            with open(OUT, "a") as f:
                f.write(json.dumps(res) + "\n")
            n[0] += 1
            if n[0] % 50 == 0:
                print(f"{n[0]}/{len(todo)}", flush=True)

    with ThreadPoolExecutor(max_workers=int(sys.argv[1]) if len(sys.argv) > 1 else 12) as ex:
        list(ex.map(work, todo))
    print("done", flush=True)


if __name__ == "__main__":
    main()
