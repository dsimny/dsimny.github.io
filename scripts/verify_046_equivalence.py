"""Build a transaction that installs the PRE-046 view definitions alongside the
current ones and proves the two produce byte-identical row multisets.

Both versions run inside ONE transaction so now() -- and therefore the TTL
window and the pre-kickoff gate -- is identical for both. Rolled back at the end;
nothing is persisted and the captured slate is untouched."""
import pathlib

f45 = pathlib.Path("db/migrations/045_p4_total_price_order.sql").read_text(encoding="utf-8")
f41 = pathlib.Path("db/migrations/041_p4_market_intelligence.sql").read_text(encoding="utf-8")

def body(text, view, header):
    i = text.index(header)
    j = text.index(f"COMMENT ON VIEW public.{view}", i)
    return text[i:j].rstrip().rstrip(";")

old = {
    "canonical_market":  body(f45, "canonical_market",
                              "CREATE OR REPLACE VIEW public.canonical_market"),
    "market_movement":   body(f45, "market_movement",
                              "CREATE OR REPLACE VIEW public.market_movement"),
    "executable_market": body(f45, "executable_market",
                              "CREATE OR REPLACE VIEW public.executable_market"),
    "market_intelligence": body(f41, "market_intelligence",
                                "CREATE VIEW public.market_intelligence"),
}

VIEWS = ["canonical_market", "market_movement", "executable_market",
         "market_intelligence"]

out = [
    "BEGIN;",
    "SET LOCAL statement_timeout='900s';",
    "UPDATE public.system_settings SET snapshot_ttl_seconds=86400 WHERE id;",
    "CREATE SCHEMA zz_old;",
]

# Dependency order: canonical -> executable/movement -> intelligence
for v in VIEWS:
    src = old[v]
    src = src.replace(f"CREATE OR REPLACE VIEW public.{v}", f"CREATE VIEW zz_old.{v}", 1)
    src = src.replace(f"CREATE VIEW public.{v}", f"CREATE VIEW zz_old.{v}", 1)
    # point inter-view references at the OLD copies
    for dep in VIEWS:
        src = src.replace(f"public.{dep}", f"zz_old.{dep}")
    out.append(src + ";")

# Byte-exact equivalence: symmetric difference of the full-row JSON multisets.
# EXCEPT ALL both ways catches differing values, differing row counts, and
# duplicate-multiplicity changes. No column has to be named, so nothing can be
# quietly left out of the comparison.
for v in VIEWS:
    out.append(f"""
SELECT '{v}' AS view,
       (SELECT count(*) FROM public.{v})  AS new_rows,
       (SELECT count(*) FROM zz_old.{v})  AS old_rows,
       (SELECT count(*) FROM (
            (SELECT to_jsonb(t) AS r FROM public.{v} t
             EXCEPT ALL
             SELECT to_jsonb(t) FROM zz_old.{v} t)
            UNION ALL
            (SELECT to_jsonb(t) FROM zz_old.{v} t
             EXCEPT ALL
             SELECT to_jsonb(t) FROM public.{v} t)
       ) d)                                AS differing_rows;""")

out.append("ROLLBACK;")
print("\n".join(out))
