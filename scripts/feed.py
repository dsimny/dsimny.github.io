#!/usr/bin/env python3
"""
Open Ledger Sports — public RSS feed of the free pick.

One item per day: the Free Pick of the Day, in full, the same content that is
already public on the site and in #free-pick. NEVER the premium plays — this
file is served at openledgersports.com/feed.xml and is world-readable, so it
holds only what is already free.

Items accumulate in data/feed_items.json (append-only, one per date, last 60
kept) and feed.xml is regenerated from it. When beehiiv's RSS-to-Send is turned
on later, it points at feed.xml and mails each new item with no further code.

Since the daily blog shipped, the feed also carries one item per Morning Line
post (title + teaser + link, guid olsb-<date>; the pick items keep olsp-<date>).
Blog items live in data/blog_items.json (written by blog.py) and are merged in
by _write_xml on EVERY regeneration, so whichever workflow rewrites feed.xml
last — morning board, grading, rebuild — the blog items survive. send_email.py
is unaffected: it reads data/feed_items.json, which stays free-pick-only.
"""
import html as _html
import json
import os
from datetime import datetime, timezone

CHANNEL_TITLE = "Open Ledger Sports · Free Pick of the Day"
CHANNEL_DESC = ("One free MLB pick each morning, in full, before first pitch, plus "
                "The Morning Line — the daily blog. Every result on the public "
                "ledger, wins and losses alike.")
LEGAL = ("Open Ledger Sports is an analytics publication, not a sportsbook. Not "
         "betting advice. 21+. If you or someone you know has a gambling problem, "
         "call or text 1-800-GAMBLER.")

KEEP = 60   # roughly two months of daily items


def _rfc822(iso_utc):
    """ISO 8601 UTC -> RFC 822, which RSS requires for pubDate."""
    try:
        dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        dt = datetime.now(timezone.utc)
    # %d and %H are zero-padded, which RFC 822 allows, so no glibc %-d needed.
    return dt.strftime("%a, %d %b %Y %H:%M:%S GMT")


def build_item_html(free, nice_date, analysis_html, site_url, daily=None):
    """The email/feed body for one day: the free pick, else the Daily Pick
    (v0.15 — the always-on strategy, clearly labeled 0u-proving), else the
    honest no-play message. `daily` is the Daily Pick's BOARD ROW — public by
    the engine's candidate rule, never a held play."""
    site = site_url or "https://openledgersports.com"
    if free is None and daily is not None:
        matchup = _html.escape(daily["matchup"])
        pick = _html.escape(daily["pick"])
        edge = (f' · edge {daily["edge"]*100:+.1f} pts vs the {daily["mkt_odds"]:+d} price'
                if daily.get("mkt_odds") is not None else "")
        body = (f"<p><strong>🎯 Daily Pick: {matchup}</strong><br>"
                f"<strong>Pick:</strong> {pick} · {daily['confidence']*100:.1f}% of "
                f"{daily['n_sims']:,} sims{edge}<br>"
                f"<em>No play cleared the strict Qualified gates today. The Daily Pick is the "
                f"always-on strategy's top-ranked candidate under a lower, precommitted bar — "
                f"published at 0 units through its proving window (ends September 8) and graded "
                f"on its own public record.</em></p>") + analysis_html
        title = f"Daily Pick for {nice_date}: {daily['pick']}"
        footer = (f'<p><a href="{site}">See the full board and the running record →</a></p>'
                  f'<p style="font-size:12px;color:#888;">{LEGAL}</p>')
        return body + footer
    if free is None:
        body = (f"<p><strong>No qualifying plays today.</strong> The engine ran the full "
                f"slate and nothing cleared the circuit breakers and the edge gate at an "
                f"allocatable price — and no candidate survived the Daily Pick's eligibility "
                f"rules either. We don't manufacture a pick to fill the slot. "
                f"Passing is a position too.</p>")
        title = f"Free Pick for {nice_date}: no qualifying plays"
    else:
        matchup = _html.escape(free["matchup"])
        pick = _html.escape(free["pick"])
        conf = free["confidence"] * 100
        edge = (f' · edge {free["edge"]*100:+.1f} pts vs the {free["mkt_odds"]:+d} price'
                if free.get("mkt_odds") is not None else "")
        header = (f"<p><strong>{matchup}</strong><br>"
                  f"<strong>Pick:</strong> {pick} · "
                  f"<strong>{conf:.1f}%</strong> of {free['n_sims']:,} sims{edge}<br>"
                  f"<em>A strong play, but not our Play of the Day: the top-confidence "
                  f"plays go to premium members. This one is free and in full.</em></p>")
        body = header + analysis_html
    footer = (f'<p><a href="{site}">See the full board and the running record →</a></p>'
              f'<p style="font-size:12px;color:#888;">{LEGAL}</p>')
    return body + footer


def update(root, date, free, nice_date, analysis_html, generated_utc, site_url, daily=None):
    """Append today's item and rewrite feed.xml. Idempotent by date.

    Never edits an item already published for a date, so the grading rebuild the
    next morning (which re-runs build_site against an earlier board) leaves the
    existing feed untouched.
    """
    items_path = os.path.join(root, "data", "feed_items.json")
    store = {"items": []}
    if os.path.exists(items_path):
        with open(items_path, encoding="utf-8") as f:
            store = json.load(f)

    if not any(it["date"] == date for it in store["items"]):
        if free is not None:
            title = f"Free Pick for {nice_date}: {free['pick']}"
        elif daily is not None:
            title = f"Daily Pick for {nice_date}: {daily['pick']}"
        else:
            title = f"Free Pick for {nice_date}: no qualifying plays"
        store["items"].append({
            "date": date,
            "title": title,
            "html": build_item_html(free, nice_date, analysis_html, site_url, daily=daily),
            "pubDate": _rfc822(generated_utc),
        })
        store["items"] = sorted(store["items"], key=lambda it: it["date"])[-KEEP:]
        os.makedirs(os.path.dirname(items_path), exist_ok=True)
        with open(items_path, "w", encoding="utf-8") as f:
            json.dump(store, f, indent=1)

    _write_xml(root, store["items"], site_url)


def rebuild(root, site_url=""):
    """Regenerate feed.xml from the stores without adding anything.

    blog.py calls this after updating data/blog_items.json; safe from any
    workflow at any hour because it only re-renders what is already public.
    """
    items_path = os.path.join(root, "data", "feed_items.json")
    store = {"items": []}
    if os.path.exists(items_path):
        with open(items_path, encoding="utf-8") as f:
            store = json.load(f)
    _write_xml(root, store["items"], site_url)


def _blog_items(root):
    """The Morning Line items, as feed entries: title + teaser + link. The
    teaser is enough for a reader/ESP; the full article lives on its page."""
    path = os.path.join(root, "data", "blog_items.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        blog = json.load(f)
    return blog.get("items", [])


def _write_xml(root, items, site_url):
    site = (site_url or "https://openledgersports.com").rstrip("/")
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>',
             f"<title>{_html.escape(CHANNEL_TITLE)}</title>",
             f"<link>{site}</link>",
             f"<description>{_html.escape(CHANNEL_DESC)}</description>",
             "<language>en-us</language>"]
    entries = [
        {"date": it["date"], "sort": (it["date"], 0), "title": it["title"],
         "link": f"{site}/#free", "guid": f'olsp-{it["date"]}',
         "pubDate": it["pubDate"], "html": it["html"]}
        for it in items
    ] + [
        {"date": it["date"], "sort": (it["date"], 1),
         # Only the daily slate column carries the Morning Line name. A feature
         # post is not an edition of it and must not be filed as one.
         "title": (f'The Morning Line: {it["title"]}'
                   if it.get("kind", "slate") in ("slate", "evergreen")
                   else it["title"]),
         "link": f'{site}/blog/{it["date"]}.html', "guid": f'olsb-{it["date"]}',
         "pubDate": it["pubDate"],
         "html": (f'<p>{_html.escape(it["teaser"])}</p>'
                  f'<p><a href="{site}/blog/{it["date"]}.html">Read the post →</a></p>')}
        for it in _blog_items(root)
    ]
    for it in sorted(entries, key=lambda i: i["sort"], reverse=True):
        parts += [
            "<item>",
            f"<title>{_html.escape(it['title'])}</title>",
            f"<link>{it['link']}</link>",
            f'<guid isPermaLink="false">{it["guid"]}</guid>',
            f"<pubDate>{it['pubDate']}</pubDate>",
            # Both, for maximum reader/ESP compatibility: some read description,
            # some read content:encoded for the full HTML body.
            f"<description><![CDATA[{it['html']}]]></description>",
            f"<content:encoded><![CDATA[{it['html']}]]></content:encoded>",
            "</item>",
        ]
    parts.append("</channel></rss>")
    with open(os.path.join(root, "feed.xml"), "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
