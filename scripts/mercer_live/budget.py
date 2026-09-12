#!/usr/bin/env python3
"""
Open Ledger Sports — Mercer Live ML-1, API-credit accounting and caps.

Live polling is the first caller in this repository that can spend credits
every MINUTE, so it gets what no other caller needed: a hard local cap
enforced BEFORE each call, plus a reserve that the pregame pipelines are never
allowed to be starved below.

    daily cap      max credits this package may spend per UTC day (default 2000)
    reserve        if the provider's own `x-requests-remaining` header last
                   read below this, ML-1 stops calling (default 5000) — the
                   MLB board (~250/month) and the football T-24/close captures
                   (~208/month) are the product; shadow research is not

Both are enforced in `Budget.allow()`, which the capture loop consults before
every odds call, and every decision is written into the tick record so a
skipped call is as auditable as a made one.

WHY A LOCAL LEDGER AND NOT ONLY THE PROVIDER HEADER. The header is the truth
about the account, but it is shared with three other callers and it is only
observed AFTER a call. A local per-day count is what stops a runaway loop
(a bug that thinks a game is live all night) from spending the account
before anyone looks. The two are complementary and both are recorded.

The spend is also appended to data/odds_credits.json through
fetch_odds.record_credits, source `mercer_live:<league>`, so the repo-wide
credit log shows this caller by name like every other one.
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "football"))
import mlcommon as C                                  # noqa: E402

DEFAULT_DAILY_CAP = 2000
DEFAULT_RESERVE = 5000


class Budget:
    def __init__(self, path, daily_cap=DEFAULT_DAILY_CAP, reserve=DEFAULT_RESERVE):
        self.path = path
        self.daily_cap = int(daily_cap)
        self.reserve = int(reserve)
        self.state = {"days": {}, "last_remaining": None, "last_read_utc": None}
        if os.path.exists(path):
            try:
                with io.open(path, encoding="utf-8") as f:
                    self.state = json.load(f)
            except ValueError:
                pass   # an unreadable budget file is treated as empty, and the
                       # cap then applies from zero: the SAFE direction

    def spent_today(self, now):
        return int(self.state["days"].get(C.utc_date(now), {}).get("credits", 0))

    def allow(self, cost, now):
        """(ok, reason). Reason is recorded whether or not the call proceeds."""
        spent = self.spent_today(now)
        if spent + cost > self.daily_cap:
            return False, f"daily_cap ({spent}+{cost} > {self.daily_cap})"
        rem = self.state.get("last_remaining")
        if rem is not None and rem - cost < self.reserve:
            return False, f"reserve ({rem} remaining, floor {self.reserve})"
        return True, f"ok ({spent}+{cost} <= {self.daily_cap})"

    def record(self, cost, credits, now):
        day = self.state["days"].setdefault(C.utc_date(now), {"credits": 0, "calls": 0})
        day["credits"] += int(credits.get("last_call_cost") or cost)
        day["calls"] += 1
        if credits.get("remaining") is not None:
            self.state["last_remaining"] = credits["remaining"]
            self.state["last_read_utc"] = credits.get("read_utc")
        self._save()
        # The repo-wide log, so this caller is visible beside fetch_data,
        # fetch_closing and football_fetch_odds. Never raises.
        try:
            import fetch_odds
            fetch_odds.record_credits(credits)
        except Exception as exc:                       # noqa: BLE001
            print(f"NOTE: could not append to data/odds_credits.json: {exc}")

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with io.open(self.path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(self.state, f, indent=1, sort_keys=True)

    def summary(self, now):
        return {"spent_today": self.spent_today(now), "daily_cap": self.daily_cap,
                "reserve": self.reserve, "last_remaining": self.state.get("last_remaining"),
                "last_read_utc": self.state.get("last_read_utc")}


def burn_table(interval_s, markets, regions="us"):
    """Expected credit burn for the documentation and the --dry-run report."""
    from odds import call_cost
    per_call = call_cost(markets, regions)
    per_hour = 3600 / interval_s * per_call
    return {
        "per_call": per_call,
        "per_live_hour": round(per_hour),
        "nfl_sunday_10.5h": round(per_hour * 10.5),
        "nfl_week_thu_sun_mon_17.5h": round(per_hour * 17.5),
        "ncaaf_saturday_14h": round(per_hour * 14),
        "both_leagues_month_4_weeks": round(per_hour * (17.5 + 14) * 4),
    }
