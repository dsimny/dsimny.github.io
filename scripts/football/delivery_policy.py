"""Prospective delivery controls; never modify committed boards or ledgers."""
from datetime import datetime, timezone
import math

import market
import record_policy

# Incident containment. Re-enable only through a reviewed source change after
# the product/selection policy is agreed. --force must not bypass this pause.
PAUSED = True
PAUSE_REASON = "Football delivery paused for the September 13 selection audit"


def validate(board, week, now=None):
    """Reject unsafe recommendation delivery, including forced replays."""
    now = now or datetime.now(timezone.utc)
    if board.get("slate_week") != week or market.slate_week(now) != week:
        raise ValueError("wrong or stale slate week")
    if board.get("selection_version") != record_policy.VERSION:
        raise ValueError("unapproved selection version")
    if board.get("coverage_status") != "covered":
        raise ValueError("coverage requires manual review")
    if not board.get("decision_made"):
        raise ValueError("decision not made")
    identities = set()
    for g in board.get("games", []):
        identity = (g.get("sport"), g.get("matchup"))
        if identity in identities:
            raise ValueError("ambiguous repeated matchup in slate")
        identities.add(identity)
    for tier in ("premium", "free"):
        g = board.get(tier)
        if not g:
            continue
        kick = market.parse_utc(g.get("kickoff_utc"))
        if not kick or kick.tzinfo is None or kick <= now or market.slate_week(kick) != week:
            raise ValueError("selected game is started or misrouted")
        if g.get("sport") not in ("nfl", "ncaaf"):
            raise ValueError("unsupported sport")
        if (g.get("selection_version") != record_policy.VERSION
                or not g.get("selection_eligible") or not g.get("commitment_sha")):
            raise ValueError("missing eligible selection provenance")
        p = g.get("fair_side")
        price = g.get("best_price")
        if (not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 < p < 1
                or not isinstance(price, (int, float)) or not math.isfinite(price)
                or abs(price) < 100):
            raise ValueError("invalid probability or American price")
        if g.get("tier") != tier or g not in board.get("games", []):
            raise ValueError("selection is inconsistent with slate")
