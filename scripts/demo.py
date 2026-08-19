"""Print the demo-script queries (anchors substituted) plus the test JWTs.

Reads the demo anchors seeded by seed_wdp.py from the wdp database's
demo_anchors table and substitutes them into the three demo queries from
docs/specs/demo-script.md. With --json, emits only the anchors as JSON
(consumed by `make anchors` to bake services/frontend/public/anchors.json).

Usage: python scripts/demo.py --dsn postgresql://... [--json]
Env:   WDP_DATABASE_URL (fallback for --dsn), JWT_SECRET / JWT_TTL_HOURS
       (passed through to mint_jwt).
"""
import argparse
import asyncio
import json
import os
import sys

from mint_jwt import mint

ANCHOR_KEYS = (
    "query2_proposal_number",
    "query3b_person_name",
    "query3b_denied_orcid",
)


async def fetch_anchors(dsn: str) -> dict[str, str]:
    import asyncpg  # deferred so import stays cheap without it

    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch("SELECT key, value FROM demo_anchors")
    finally:
        await conn.close()
    return {r["key"]: r["value"] for r in rows}


def render(anchors: dict[str, str]) -> str:
    proposal = anchors["query2_proposal_number"]
    person = anchors["query3b_person_name"]
    denied = anchors["query3b_denied_orcid"]
    lines = [
        "Demo queries (docs/specs/demo-script.md, anchors substituted)",
        "=============================================================",
        "",
        "Query 1 — local-only aggregate      [user: analyst-full]",
        "  How many proposals had Prohibited Factors on factor 4 in fiscal"
        " year 2025? Break it down by reviewing component.",
        "",
        "Query 1b — follow-up, same conversation [user: analyst-full]",
        "  How does that compare to fiscal year 2024?",
        "",
        "Query 2 — cross-boundary join       [user: analyst-full]",
        f"  Give me research background on the personnel of proposal {proposal}.",
        "",
        "Query 3A — scoped user, same query  [user: analyst-local]",
        f"  Give me research background on the personnel of proposal {proposal}.",
        "",
        "Query 3B — WDP-side denial          [user: analyst-full]",
        f"  What research background do we have on {person}?",
        f"  (denied ORCID {denied}; FAKE_WDP_DENY_ORCIDS must include it)",
        "",
        "Test JWTs",
        "=========",
    ]
    for user in ("analyst-full", "analyst-local"):
        lines += [f"{user}:", f"  {mint(user)}", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=os.environ.get("WDP_DATABASE_URL"))
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    if not args.dsn:
        sys.exit("demo.py: provide --dsn or set WDP_DATABASE_URL")

    anchors = asyncio.run(fetch_anchors(args.dsn))
    missing = [k for k in ANCHOR_KEYS if k not in anchors]
    if missing:
        sys.exit(
            f"demo.py: demo_anchors missing {missing} — run `make seed` first"
        )
    if args.as_json:
        print(json.dumps({k: anchors[k] for k in ANCHOR_KEYS}, indent=2))
    else:
        print(render(anchors))


if __name__ == "__main__":
    main()
