#!/usr/bin/env python3
"""Render Kelly's Daily Edit from JSON, so nobody hand-writes the HTML.

Inputs (repo root):
  data/stories.json   written each day: stocks + stories per section (see docs below)
  data/sales.json     sales table rows; expired rows are dropped automatically. On Mon/Thu research days edit rows, "researched" and "note"
  data/assets.json    verified Wikimedia Commons logos and photos, keyed by id
  data/footer.html    footer note
  tools/style.css, tools/canvas.json

Outputs:
  build/project/Main.dc.html + build/project/canvas.json   (publish these to the Design artifact)
  index.html                                               (standalone phone page for GitHub Pages)

Usage: python3 tools/build.py [--date YYYY-MM-DD] [--stories PATH] [--out-dir DIR] [--standalone PATH]

stories.json shape:
{
  "stocks_caption": "Trending stocks, Fri 13 Nov US close. Prices where sources gave them.",
  "stocks": [{"ticker": "NVDA", "price": "$162.85", "move": "-2.01%"}, ...],   # logo looked up in assets.json
  "sections": {
    "01": {"title": "Serif section title.", "stories": [
        {"headline": "...", "body": "1-3 sentences.", "trend": "one sentence",
         "sources": [{"label": "CNBC", "url": "https://..."}],
         "photo": "nyse" | {"file": "X.jpg", "alt": "..."} | null,
         "note": "optional italic caveat, e.g. Reported from a paywalled article.",
         "disclosure": "optional, e.g. Anthropic makes Claude."} ]},
    "02": {...}, "03": {...}, "04": {...}
  },
  "footer_extra": "optional sentence added to the footer"
}
The first story of each section is the lead. body + trend together must be at most 4 sentences.
"""
import argparse
import datetime
import html
import json
import os
import re
import sys
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMMONS = "https://commons.wikimedia.org/wiki/Special:FilePath/"
FONT_LINK = ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600'
             '&family=Newsreader:opsz,wght@6..72,300;6..72,400&display=swap">')
KICKERS = {
    "01": "01 &mdash; Finance &amp; Markets",
    "02": "02 &mdash; World &amp; New Zealand",
    "03": "03 &mdash; Social, Fashion, Pop Culture &amp; Celebrity",
    "04": "04 &mdash; Auckland &mdash; Eat, New &amp; This Week",
}


def esc(s):
    return html.escape(str(s), quote=True)


def read(path):
    with open(os.path.join(ROOT, path), encoding="utf8") as f:
        return f.read()


def load_json(path):
    return json.loads(read(path))


def n_sentences(text):
    parts = re.split(r"(?<=[.!?])[\"'”’)]*\s+(?=[A-Z0-9“\"'(])", text.strip())
    return len([p for p in parts if p.strip()])


def photo_html(photo, assets, warn, where):
    if not photo:
        return None
    if isinstance(photo, str):
        if photo not in assets["photos"]:
            warn(f"{where}: unknown photo id '{photo}' (add it to data/assets.json); using no photo")
            return None
        fname, alt = assets["photos"][photo]
    else:
        fname, alt = photo["file"], photo.get("alt", "")
    return (f'<div class="ph"><img src="{COMMONS}{esc(fname)}?width=1000" alt="{esc(alt)}" '
            f'loading="lazy"></div>')


def story_html(st, lead, assets, warn, where):
    ph = photo_html(st.get("photo"), assets, warn, where)
    cls = "card lead" if lead else "card"
    if not ph:
        cls += " noimg"
    body = esc(st["body"])
    if st.get("trend"):
        body += f' <b>The trend:</b> {esc(st["trend"])}'
    if st.get("note"):
        body += f' <i>{esc(st["note"])}</i>'
    if st.get("disclosure"):
        body += f' <i>Disclosure: {esc(st["disclosure"])}</i>'
    n = n_sentences(st["body"] + " " + st.get("trend", ""))
    if n > 4:
        warn(f"{where}: {n} sentences in body+trend (max 4): {st['headline'][:50]}")
    links = " &middot; ".join(
        f'<a href="{esc(s["url"])}">{esc(s["label"])}</a>' for s in st.get("sources", []))
    if not links:
        warn(f"{where}: no source link: {st['headline'][:50]}")
    return (f'<article class="{cls}">{ph or ""}<div class="tx"><h3>{esc(st["headline"])}</h3>'
            f'<p>{body}</p><p class="src">{links}</p></div></article>')


def strip_html(data, assets):
    cells = []
    for s in data.get("stocks", []):
        t = s["ticker"]
        logo = assets["logos"].get(t)
        img = f'<img src="{COMMONS}{esc(logo)}?width=200" alt="{esc(t)} logo">' if logo else ""
        move = str(s.get("move", ""))
        cls = "dn" if move[:1] in "-−" else "up"
        cells.append(f'<div class="st">{img}<span class="tk">{esc(t)}</span>'
                     f'<b>{esc(s.get("price") or "—")}</b><span class="{cls}">{esc(move)}</span></div>')
    cap = data.get("stocks_caption", "")
    return (f'<div class="strip" aria-label="Five trending stocks">{"".join(cells)}</div>'
            f'<p class="cap">{esc(cap)}</p>')


def sales_html(sales, today, short_date, warn):
    rows, dropped = [], []
    for r in sales["rows"]:
        exp = r.get("expires")
        if exp and datetime.date.fromisoformat(exp) < today:
            dropped.append(r["store"])
            continue
        rows.append("<tr>" + "".join(
            f"<td>{r[k]}</td>" for k in ("store", "offer", "code", "deadline", "restrictions")) + "</tr>")
    if dropped:
        warn(f"sales: dropped {len(dropped)} expired row(s): {', '.join(dropped)}")
    head = "".join(f"<th>{h}</th>" for h in
                   ("Store", "Offer", "Code or verified link", "Deadline", "Restrictions"))
    return ('<section class="sec"><div class="sech"><div class="kick">05 &mdash; Sales this week</div>'
            "<h2>What&rsquo;s worth buying in NZ.</h2></div>"
            f'<div class="tw"><table class="tbl"><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}'
            f'</tbody></table></div><p class="cap">Last checked {short_date}; last fresh retailer research {sales["researched"]}. {sales["note"]}</p></section>')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (default: today in Pacific/Auckland)")
    ap.add_argument("--stories", default=os.path.join(ROOT, "data", "stories.json"))
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "build"))
    ap.add_argument("--standalone", default=os.path.join(ROOT, "index.html"))
    args = ap.parse_args()

    warnings = []
    warn = warnings.append
    today = (datetime.date.fromisoformat(args.date) if args.date
             else datetime.datetime.now(ZoneInfo("Pacific/Auckland")).date())
    with open(args.stories, encoding="utf8") as f:
        data = json.load(f)
    assets, sales = load_json("data/assets.json"), load_json("data/sales.json")

    long_date = f"{today.strftime('%A')} {today.day} {today.strftime('%B %Y')}"
    short_date = f"{today.strftime('%a')} {today.day} {today.strftime('%b %Y')}"
    title = f"Kelly’s Daily Edit — {short_date}"

    sections, total = [], 0
    for key in ("01", "02", "03", "04"):
        sec = data["sections"].get(key)
        if not sec or not sec.get("stories"):
            warn(f"section {key}: no stories")
            continue
        total += len(sec["stories"])
        cards = "".join(story_html(st, i == 0, assets, warn, f"{key}.{i + 1}")
                        for i, st in enumerate(sec["stories"]))
        sections.append(
            f'<section class="sec"><div class="sech"><div class="kick">{KICKERS[key]}</div>'
            f'<h2>{esc(sec.get("title", ""))}</h2></div><div class="grid">{cards}</div></section>')
    if not 8 <= total <= 12:
        warn(f"story count {total} is outside the 8-12 target")

    footer = read("data/footer.html").strip()
    if data.get("footer_extra"):
        footer = esc(data["footer_extra"]) + " " + footer
    body = (
        '<div class="mag"><div class="top"><span>Auckland &middot; New Zealand</span>'
        "<span>Daily edition &middot; 7:44am</span></div>"
        f'<header class="mast"><h1>Kelly&rsquo;s Daily Edit</h1><p>{long_date}</p></header>'
        f'{strip_html(data, assets)}{"".join(sections)}{sales_html(sales, today, short_date, warn)}'
        f'<footer class="foot">{footer}</footer></div>')

    css = read("tools/style.css")
    est_h = 900 + 215 * total + 60 * len(sales["rows"])

    script = ('<script type="text/x-dc" data-dc-script data-props=\'{"$preview":{"width":1280,"height":__H__}}\'>\n'
              "class Component extends DCLogic {\nrenderVals() { return {}; }\n}\n</script>\n").replace("__H__", str(est_h))
    dc = (f'<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<title>{html.escape(title)}</title>\n'
          '<script src="./support.js"></script>\n</head>\n<body>\n<x-dc>\n<helmet>\n'
          f"{FONT_LINK}\n<style>\n{css}</style>\n</helmet>\n{body}\n</x-dc>\n{script}</body>\n</html>\n")

    blank = re.sub(r"<a (?![^>]*target=)", '<a target="_blank" rel="noopener noreferrer" ', body)
    standalone = (f'<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
                  '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
                  f"<title>{html.escape(title)}</title>\n{FONT_LINK}\n<style>\n{css}</style>\n</head>\n"
                  f"<body>\n{blank}\n</body>\n</html>\n")

    canvas = read("tools/canvas.json").replace("__TITLE__", title).replace("__H__", str(est_h))
    json.loads(canvas)  # sanity check

    proj = os.path.join(args.out_dir, "project")
    os.makedirs(proj, exist_ok=True)
    for path, text in ((os.path.join(proj, "Main.dc.html"), dc),
                       (os.path.join(proj, "canvas.json"), canvas),
                       (args.standalone, standalone)):
        with open(path, "w", encoding="utf8") as f:
            f.write(text)

    for w in warnings:
        print("WARN:", w, file=sys.stderr)
    print(f"built {title}: {total} stories; {len(dc)} bytes dc, {len(standalone)} bytes standalone; "
          f"artifact root: {args.out_dir}", file=sys.stderr)


if __name__ == "__main__":
    main()
