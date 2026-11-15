#!/usr/bin/env python3
"""Collect and filter RSS feeds for Kelly's Daily Edit using feedparser.

Usage:
  python3 tools/feeds.py                    # fetch live feeds, print JSON candidates
  python3 tools/feeds.py --offline DIR      # parse DIR/<feed-key>.xml instead of fetching
  python3 tools/feeds.py --section 01 --max 6

Output: JSON {section: [candidate, ...]} on stdout, a feed health report on stderr.
Each candidate: title, link, published (UTC ISO), source, summary (<=200 chars).
"""
import argparse
import calendar
import html
import json
import os
import re
import sys
import time

import feedparser

AGENT = "KellyDailyEdit/1.0 (+https://chellyka.github.io/kellys-daily-edit/)"

# key -> (url, source label)
FEEDS = {
    "cnbc": ("https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664", "CNBC"),
    "marketwatch": ("https://feeds.content.dowjones.io/public/rss/mw_topstories", "MarketWatch"),
    "interest": ("https://www.interest.co.nz/rss", "interest.co.nz"),
    "bbc": ("https://feeds.bbci.co.uk/news/world/rss.xml", "BBC News"),
    "aljazeera": ("https://www.aljazeera.com/xml/rss/all.xml", "Al Jazeera"),
    "nzherald_nz": ("https://www.nzherald.co.nz/arc/outboundfeeds/rss/section/nz/?outputType=xml", "NZ Herald"),
    "variety": ("https://variety.com/feed/", "Variety"),
    "thr": ("https://www.hollywoodreporter.com/feed/", "The Hollywood Reporter"),
    "vogue": ("https://www.vogue.com/feed/rss", "Vogue"),
    "nzherald_akl": ("https://www.nzherald.co.nz/arc/outboundfeeds/rss/section/nz/auckland/?outputType=xml", "NZ Herald"),
    "concrete_akl": ("https://concreteplayground.com/auckland/feed", "Concrete Playground"),
}

# section -> config
SECTIONS = {
    "01": {
        "title": "Finance & Markets",
        "feeds": ["cnbc", "marketwatch", "interest"],
        "max_age_h": 36,
        "include": r"\b(stocks?|markets?|nasdaq|s&p|dow|fed|rates?|inflation|yields?|oil|nvidia|amd|apple|tesla|meta|anthropic|ipo|earnings|nzx|rbnz|economy|economic|bank|banks|asia|nikkei|china|house prices?|dollar|ai)\b",
        "exclude": r"i'm \d+|i inherited|my (husband|wife|partner|mother|father)|should (i|a|my)|am i wrong|roof deck|retire|widow|stepkid|inherit|postnup|quiz",
    },
    "02": {
        "title": "World & New Zealand",
        "feeds": ["bbc", "aljazeera", "nzherald_nz"],
        "max_age_h": 36,
        "include": r".",
        "exclude": r"quiz|\bwatch:|football|rugby|cricket|netball|golf|horoscope|sport",
    },
    "03": {
        "title": "Social, Fashion, Pop Culture & Celebrity",
        "feeds": ["variety", "thr", "vogue"],
        "max_age_h": 72,
        "include": r".",
        "exclude": r"where to (watch|stream)|livestream|obituary|dies at|best deals",
    },
    "04": {
        "title": "Auckland",
        "feeds": ["nzherald_akl", "concrete_akl"],
        "max_age_h": 14 * 24,
        "include": r"restaurant|cafe|bar\b|bakery|open|opening|menu|chef|food|drink|festival|feast|market|gig|concert|theatre|exhibition|event|show|tour|wine|brunch|dinner",
        "exclude": r"police|arrest|crash|fire|court|murder|assault|shooting|motorway|firearms|drug",
    },
}


def clean(text, n=300):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def entry_time(e):
    t = e.get("published_parsed") or e.get("updated_parsed")
    return calendar.timegm(t) if t else None


def load(key, offline_dir):
    url, label = FEEDS[key]
    if offline_dir:
        path = os.path.join(offline_dir, key + ".xml")
        if not os.path.exists(path):
            return None, f"missing offline file {path}", label
        d = feedparser.parse(path)
    else:
        d = feedparser.parse(url, agent=AGENT)
    status = d.get("status")
    if not d.entries:
        why = repr(d.get("bozo_exception")) if d.bozo else f"no entries (HTTP {status})"
        return None, why, label
    if status and status >= 400:
        return None, f"HTTP {status}", label
    return d, "ok" + (" (bozo)" if d.bozo else ""), label


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", help="directory of <feed-key>.xml files to parse instead of fetching")
    ap.add_argument("--section", help="only this section, e.g. 01")
    ap.add_argument("--max", type=int, default=5, help="max candidates per section")
    args = ap.parse_args()

    now = time.time()
    out, report, seen = {}, [], set()
    cache = {}
    for sec, cfg in SECTIONS.items():
        if args.section and sec != args.section:
            continue
        inc, exc = re.compile(cfg["include"], re.I), re.compile(cfg["exclude"], re.I)
        items = []
        for key in cfg["feeds"]:
            if key not in cache:
                cache[key] = load(key, args.offline)
                d, status, _ = cache[key]
                report.append(f"{key}: {status}" + (f", {len(d.entries)} items" if d else ""))
            d, status, label = cache[key]
            if not d:
                continue
            for e in d.entries:
                ts = entry_time(e)
                title, link = clean(e.get("title"), 200), e.get("link", "")
                if not title or not link or ts is None:
                    continue
                if now - ts > cfg["max_age_h"] * 3600 or ts - now > 3600:
                    continue
                blob = title + " " + clean(e.get("summary"), 500)
                if exc.search(title) or not inc.search(blob):
                    continue
                norm = re.sub(r"\W+", "", title.lower())
                if link in seen or norm in seen:
                    continue
                seen.update([link, norm])
                items.append({
                    "title": title,
                    "link": link,
                    "published": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime(ts)),
                    "source": label,
                    "summary": clean(e.get("summary"), 200),
                    "_ts": ts,
                })
        items.sort(key=lambda x: x["_ts"], reverse=True)
        # interleave sources so one feed does not crowd out the rest
        by_src, picked = {}, []
        for it in items:
            by_src.setdefault(it["source"], []).append(it)
        while len(picked) < args.max and any(by_src.values()):
            for s in list(by_src):
                if by_src[s] and len(picked) < args.max:
                    picked.append(by_src[s].pop(0))
        for it in picked:
            it.pop("_ts")
        out[sec] = picked

    print("\n".join(report), file=sys.stderr)
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
    print()


if __name__ == "__main__":
    main()
