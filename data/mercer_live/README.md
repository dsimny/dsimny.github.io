# data/mercer_live — Mercer Live ML-1 observation store

Research-only. Nothing in this directory is a pick, a unit, a recommendation
or a ledger. It is the append-only DATA step of Mercer Live, whose rules are
frozen in `docs/MERCER_LIVE_V0.1_PREREGISTRATION.md`.

| path | committed? | what |
|---|---|---|
| `raw/<sport>/<ET date>/<kind>_<ET hour>.jsonl` | **no** (gitignored) | one JSON record per line: `game_state`, `market_event`, `market_quote`; never rewritten |
| `raw/runs/<ET date>/<run_id>.json` | **no** | one record per capture execution: what was fetched, decided, stored, spent, and every error |
| `digest/<ET date>.json` | yes | SHA-256, bytes, lines and observed_at span of every raw shard for that day, plus run and credit counts |

Written only by `scripts/mercer_live/capture.py`. Read by nothing in
`scripts/football/` or the MLB pipeline. Schema: `docs/MERCER_LIVE_ML1_ARCHITECTURE.md`.

The raw stream must be kept on persistent storage by whoever runs the capture;
the digest is what lets anyone holding those bytes prove they are the bytes
that were observed.
