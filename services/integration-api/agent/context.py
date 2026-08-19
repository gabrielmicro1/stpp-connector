"""Planner inputs from rfff_seed: the planner_context artifact (field
dictionary + observed enums + caveats — invariant 10), the observed-enum
sets for the plan validator, and catalog entries keyword-matched to the
query (catalog-first planning; simple ILIKE per the plan-format spec, top
PLANNER_MAX_MATCHES).
"""
import json
import re
from dataclasses import dataclass
from typing import Protocol

import asyncpg

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9-]*")
_STOPWORDS = {
    "the", "and", "for", "had", "has", "have", "was", "were", "are", "with",
    "what", "which", "who", "whom", "how", "many", "much", "give", "get",
    "show", "list", "find", "tell", "about", "did", "does", "down", "break",
    "all", "any", "per", "our", "their", "its", "this", "that", "these",
    "those", "from", "into", "onto", "year", "years", "you", "can", "please",
}

PROPOSAL_MATCH_SQL = """
SELECT proposal_number, proposal_title, submitting_entity_name, fiscal_year
FROM proposals
WHERE proposal_title ILIKE ANY($1::text[])
   OR submitting_entity_name ILIKE ANY($1::text[])
   OR proposal_number ILIKE ANY($1::text[])
ORDER BY proposal_number
LIMIT $2
"""

PERSONNEL_MATCH_SQL = """
SELECT p.person_orcid, p.first_name, p.last_name,
       array_agg(DISTINCT pp.proposal_number) AS proposal_numbers
FROM personnel p
JOIN proposal_personnel pp USING (person_orcid)
WHERE p.first_name ILIKE ANY($1::text[])
   OR p.last_name ILIKE ANY($1::text[])
GROUP BY p.person_orcid, p.first_name, p.last_name
ORDER BY p.person_orcid
LIMIT $2
"""


@dataclass(frozen=True)
class PlannerInputs:
    planner_context: dict
    catalog_matches: list
    observed_enums: dict


class ContextProvider(Protocol):
    async def load(self, query: str) -> PlannerInputs: ...


def extract_terms(query: str) -> list:
    """Lowercased keyword terms for the catalog match: tokens of letters,
    digits, and hyphens (so proposal numbers like p-2025-0042 survive
    whole), at least 3 chars, stopwords dropped, order-preserving dedup."""
    seen = []
    for token in _TOKEN_RE.findall(query.lower()):
        if len(token) >= 3 and token not in _STOPWORDS and token not in seen:
            seen.append(token)
    return seen


def build_observed_enums(rows) -> dict:
    """(field_name, value) rows -> {field_name: {values}}."""
    enums: dict = {}
    for row in rows:
        enums.setdefault(row["field_name"], set()).add(row["value"])
    return enums


def proposal_match(row) -> dict:
    return {
        "type": "proposal",
        "proposal_number": row["proposal_number"],
        "proposal_title": row["proposal_title"],
        "submitting_entity_name": row["submitting_entity_name"],
        "fiscal_year": row["fiscal_year"],
    }


def person_match(row) -> dict:
    return {
        "type": "person",
        "person_orcid": row["person_orcid"],
        "name": " ".join(filter(None, [row["first_name"], row["last_name"]])),
        "proposal_numbers": list(row["proposal_numbers"]),
    }


class PostgresContextProvider:
    """Thin asyncpg reader; one connection per load (two-to-four small
    queries per job at demo scale). SQL verified live, like
    PostgresJobStore."""

    def __init__(self, dsn: str, max_matches: int) -> None:
        self._dsn = dsn
        self._max_matches = max_matches

    async def load(self, query: str) -> PlannerInputs:
        conn = await asyncpg.connect(self._dsn)
        try:
            raw_context = await conn.fetchval(
                "SELECT context FROM planner_context WHERE id"
            )
            planner_context = (
                json.loads(raw_context) if isinstance(raw_context, str)
                else (raw_context or {})
            )
            enum_rows = await conn.fetch("SELECT field_name, value FROM observed_enums")
            matches: list = []
            terms = extract_terms(query)
            if terms:
                patterns = [f"%{t}%" for t in terms]
                proposal_rows = await conn.fetch(
                    PROPOSAL_MATCH_SQL, patterns, self._max_matches
                )
                matches.extend(proposal_match(r) for r in proposal_rows)
                remaining = self._max_matches - len(matches)
                if remaining > 0:
                    person_rows = await conn.fetch(
                        PERSONNEL_MATCH_SQL, patterns, remaining
                    )
                    matches.extend(person_match(r) for r in person_rows)
            return PlannerInputs(
                planner_context=planner_context,
                catalog_matches=matches,
                observed_enums=build_observed_enums(enum_rows),
            )
        finally:
            await conn.close()
