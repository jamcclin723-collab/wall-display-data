#!/usr/bin/env python3
"""Writes briefing.js next to this script: headlines, Colorado gas price, and the S&P 500.
No AI and no API key needed. Schedule it to run every 15 minutes (see setup steps).
Headlines refresh about hourly; gas and the S&P refresh on every run (schedule it every 30 minutes)."""
import datetime, html, json, os, re, sys, urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

UA = "Mozilla/5.0 (wall-display)"
GN = "https://news.google.com/rss/search?hl=en-US&gl=US&ceid=US:en&q="
# topic -> list of (source name, feed address). Earlier feeds win; later ones fill gaps.
TOPICS = [
    ("WORLD", 3, [("BBC", "https://feeds.bbci.co.uk/news/world/rss.xml"),
                  ("NPR", "https://feeds.npr.org/1004/rss.xml"),
                  ("Guardian", "https://www.theguardian.com/world/rss")]),
    ("DENVER", 2, [("Denver Post", "https://www.denverpost.com/feed/"),
                   ("Google News", GN + "Denver+Colorado+when%3A1d")]),
    ("LOCAL", 1, [("Google News", GN + "%22Castle+Rock%22+Colorado+when%3A2d"),
                  ("Google News", GN + "%22Douglas+County%22+Colorado+when%3A2d")]),
]
AAA_URL = "https://gasprices.aaa.com/?state=CO"
MAX_AGE_HOURS, MAX_LEN = 36, 100
NEWS_REFRESH_MIN, GAS_REFRESH_HOURS = 55, 0.4   # headlines about hourly; gas and S&P on every 30-minute run
SKIP = re.compile(r"\b(live|watch|listen|podcast|opinion|quiz|horoscope|sponsored|review|deals?|sale|how to|photos?)\b", re.I)
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "briefing.js")
OUT_JSON = os.path.join(HERE, "briefing.json")

def now(): return datetime.datetime.now().astimezone()
def get(url, timeout=30):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout).read()

# ---------- headlines ----------
def clean(t):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(t or ""))).strip()
def shorten(t):
    if len(t) <= MAX_LEN: return t
    return t[:MAX_LEN].rsplit(" ", 1)[0].rstrip(",;:-") + "…"
def local(tag): return tag.rsplit("}", 1)[-1]
def parse(xml_bytes):
    out = []
    for el in ET.fromstring(xml_bytes).iter():
        if local(el.tag) not in ("item", "entry"): continue
        title, when = "", None
        for ch in el:
            n = local(ch.tag)
            if n == "title": title = clean(ch.text)
            elif n in ("pubDate", "published", "updated") and ch.text and when is None:
                try: when = parsedate_to_datetime(ch.text)
                except Exception:
                    try: when = datetime.datetime.fromisoformat(ch.text.replace("Z", "+00:00"))
                    except Exception: pass
        if title: out.append((title, when))
    return out
def words(t): return {w for w in re.findall(r"[a-z0-9]+", t.lower()) if len(w) > 3}
def is_dup(t, picked):
    a = words(t)
    return any(a and b and len(a & b) / min(len(a), len(b)) > 0.6 for b in map(words, picked))
def good(title, when):
    if len(title) < 25 or SKIP.search(title): return False
    if when is not None:
        if when.tzinfo is None: when = when.replace(tzinfo=datetime.timezone.utc)
        if datetime.datetime.now(datetime.timezone.utc) - when > datetime.timedelta(hours=MAX_AGE_HOURS): return False
    return True
def split_source(title, source):
    if source == "Google News" and " - " in title:
        head, tail = title.rsplit(" - ", 1)
        if len(tail) < 40: return head, tail
    return title, source

def fetch_news():
    picked, items = [], []
    for tag, quota, feeds in TOPICS:
        got = 0
        for source, url in feeds:
            if got >= quota: break
            try: entries = parse(get(url))
            except Exception as e:
                print(f"  skipped {tag} feed ({source}): {e}"); continue
            for title, when in entries:
                if got >= quota: break
                if not good(title, when): continue
                text, src = split_source(title, source)
                if is_dup(text, picked): continue
                picked.append(text); items.append({"tag": tag, "text": shorten(text), "src": src.upper()}); got += 1
        if got < quota: print(f"  only found {got} of {quota} {tag} headlines")
    return items

# ---------- gas (AAA Colorado average) ----------
def parse_gas(page):
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(page)))
    def row(label, start=0):
        i = text.find(label, start)
        if i < 0: return None, -1
        p = re.findall(r"\$\s?(\d\.\d{3})", text[i:i + 300])[:4]
        return ([float(x) for x in p] if len(p) == 4 else None), i
    cur, i = row("Current Avg.")
    yest, _ = row("Yesterday Avg.", max(i, 0))
    if not cur or not yest: raise ValueError("AAA price table not found")
    return {"price": cur[1], "yesterday": yest[1], "updated": now().isoformat()}   # index 1 = mid-grade
def fetch_gas(): return parse_gas(get(AAA_URL).decode("utf-8", "ignore"))

# ---------- S&P 500 ----------
def spx_from(closes, price):
    if len(closes) < 6: raise ValueError("not enough price history")
    return {"price": price, "day": (price / closes[-2] - 1) * 100, "week": (price / closes[-6] - 1) * 100,
            "updated": now().isoformat()}
def fetch_spx():
    try:
        res = json.loads(get("https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC?range=1mo&interval=1d"))["chart"]["result"][0]
        closes = [c for c in res["indicators"]["quote"][0]["close"] if c is not None]
        return spx_from(closes, res["meta"].get("regularMarketPrice") or closes[-1])
    except Exception as e:
        print(f"  Yahoo failed ({e}); trying Stooq")
    rows = get("https://stooq.com/q/d/l/?s=%5Espx&i=d").decode().strip().splitlines()[1:]
    closes = [float(r.split(",")[4]) for r in rows[-8:]]
    return spx_from(closes, closes[-1])

# ---------- run ----------
def load_old():
    try:
        txt = open(OUT, encoding="utf-8").read()
        return json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
    except Exception:
        return {}
def age(iso, unit):
    try: return (now() - datetime.datetime.fromisoformat(iso)).total_seconds() / unit
    except Exception: return 1e9

def main():
    out = load_old()
    if age(out.get("news_updated"), 60) >= NEWS_REFRESH_MIN:
        items = fetch_news()
        if items: out["news"], out["news_updated"] = items, now().isoformat()
        else: print("  no headlines; keeping old ones")
    if age((out.get("gas") or {}).get("updated"), 3600) >= GAS_REFRESH_HOURS:
        try: out["gas"] = fetch_gas()
        except Exception as e: print(f"  gas price failed: {e}")
    try: out["spx"] = fetch_spx()
    except Exception as e: print(f"  S&P failed: {e}")
    if not any(k in out for k in ("news", "gas", "spx")): sys.exit("Nothing came back; no file written.")
    out["updated"] = now().isoformat()
    for path, text in ((OUT, "window.BRIEFING = " + json.dumps(out) + ";\n"), (OUT_JSON, json.dumps(out) + "\n")):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f: f.write(text)
        os.replace(tmp, path)
    print("Wrote", OUT, "|", len(out.get("news", [])), "headlines,", "gas" if "gas" in out else "no gas,", "S&P" if "spx" in out else "no S&P")

if __name__ == "__main__": main()
