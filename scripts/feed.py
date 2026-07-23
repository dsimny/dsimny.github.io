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
"""
import html as _html
import json
import os
from datetime import datetime, timezone

CHANNEL_TITLE = "Open Ledger Sports · Free Pick of the Day"
CHANNEL_DESC = ("One free MLB pick each morning, in full, before first pitch. "
                "Every result on the public ledger, wins and losses alike.")
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


def build_item_html(free, nice_date, analysis_html, site_url):
    """The email/feed body for one day. Free pick only.

    free is None on a no-play day, which is itself worth publishing: passing is
    a position, and the brand says so out loud.
    """
    site = site_url or "https://openledgersports.com"
    if free is None:
        body = (f"<p><strong>No qualifying plays today.</strong> The engine ran the full "
                f"slate and nothing cleared the circuit breakers and the edge gate at an "
                f"allocatable price. We don't manufacture a pick to fill the slot. "
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


def update(root, date, free, nice_date, analysis_html, generated_utc, site_url):
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
        if free is None:
            title = f"Free Pick for {nice_date}: no qualifying plays"
        else:
            title = f"Free Pick for {nice_date}: {free['pick']}"
        store["items"].append({
            "date": date,
            "title": title,
            "html": build_item_html(free, nice_date, analysis_html, site_url),
            "pubDate": _rfc822(generated_utc),
        })
        store["items"] = sorted(store["items"], key=lambda it: it["date"])[-KEEP:]
        os.makedirs(os.path.dirname(items_path), exist_ok=True)
        with open(items_path, "w", encoding="utf-8") as f:
            json.dump(store, f, indent=1)

    _write_xml(root, store["items"], site_url)


def _write_xml(root, items, site_url):
    site = (site_url or "https://openledgersports.com").rstrip("/")
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>',
             f"<title>{_html.escape(CHANNEL_TITLE)}</title>",
             f"<link>{site}</link>",
             f"<description>{_html.escape(CHANNEL_DESC)}</description>",
             "<language>en-us</language>"]
    for it in sorted(items, key=lambda i: i["date"], reverse=True):
        parts += [
            "<item>",
            f"<title>{_html.escape(it['title'])}</title>",
            f"<link>{site}/#free</link>",
            f'<guid isPermaLink="false">olsp-{it["date"]}</guid>',
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
