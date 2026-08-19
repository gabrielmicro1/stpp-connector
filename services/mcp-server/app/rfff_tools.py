"""Family 1 — local RFFF tools over db rfff_seed (role: rfff_reader).

SQL is built from WHITELISTED column constants only; user input is always
bound as parameters, never interpolated. Builders are pure for unit testing;
executors take the asyncpg pool.
"""
import json
from datetime import date

from .errors import ToolError
from .hygiene import envelope, truncate_strings

PROPOSAL_FILTER_FIELDS = frozenset(
    {
        "fiscal_year",
        "reviewing_component",
        "reviewing_subcomponent",
        "assessment_state",
        "mitigation_status",
        "award_state",
        "award_type",
        "review_type",
    }
)
# Person-level filters: "the proposal has at least one matching person".
# person_overall_assessment is opaque (invariant 9): exact match only.
PERSON_FILTER_FIELDS = frozenset(
    {
        "factor1_assessment",
        "factor2_assessment",
        "factor3_assessment",
        "factor4_assessment",
        "person_overall_assessment",
    }
)
AGG_PROPOSAL_FIELDS = frozenset(
    {
        "fiscal_year",
        "reviewing_component",
        "reviewing_subcomponent",
        "assessment_state",
        "mitigation_status",
        "award_state",
    }
)
AGG_PERSON_FIELDS = PERSON_FILTER_FIELDS

PROPOSAL_COLUMNS = (
    "proposal_number",
    "fiscal_year",
    "ssa",
    "opportunity_number",
    "proposal_title",
    "submitting_entity_uei",
    "submitting_entity_name",
    "review_type",
    "reviewing_component",
    "reviewing_subcomponent",
    "assessment_state",
    "approved_date",
    "review_notes",
    "mitigation_status",
    "mitigation_strategy_proposal",
    "mitigation_explanation",
    "award_type",
    "award_state",
    "fain",
    "awarded_date",
    "award_pop_start",
    "award_pop_end",
)

PERSONNEL_COLUMNS = (
    "person_orcid",
    "first_name",
    "middle_name",
    "last_name",
    "proposal_role",
    "affiliation_uei",
    "affiliation_name",
    "factor1_assessment",
    "factor2_assessment",
    "factor3_assessment",
    "factor4_assessment",
    "person_overall_assessment",
    "multiple_mitigation",
    "mitigation_explanation_person",
)


def _clamp_limit(limit, max_rows: int) -> int:
    if limit is None:
        return max_rows
    return min(int(limit), max_rows)


def _serialize(value):
    if isinstance(value, date):
        return value.isoformat()
    return value


def _row_dict(row, columns) -> dict:
    out = {}
    for col in columns:
        value = _serialize(row[col])
        if col in ("mitigation_strategy_proposal", "multiple_mitigation"):
            value = list(value) if value else []
        out[col] = value
    return out


def _filter_clauses(filters: dict, params: list) -> list[str]:
    clauses = []
    for field in sorted(filters):
        value = filters[field]
        params.append(value)
        n = len(params)
        if field in PROPOSAL_FILTER_FIELDS:
            clauses.append(f"p.{field} = ${n}")
        elif field in PERSON_FILTER_FIELDS:
            clauses.append(
                "EXISTS (SELECT 1 FROM proposal_personnel pp"
                " WHERE pp.proposal_number = p.proposal_number"
                f" AND pp.{field} = ${n})"
            )
        else:
            # jsonschema (additionalProperties: false) rejects these first;
            # belt and braces so the whitelist is the last word.
            raise ToolError("invalid_args", f"unknown filter field {field!r}")
    return clauses


def build_search_proposals_sql(
    filters: dict, keywords: str | None, effective_limit: int
) -> tuple[str, list]:
    params: list = []
    clauses = _filter_clauses(filters, params)
    if keywords:
        params.append(f"%{keywords}%")
        n = len(params)
        clauses.append(
            f"(p.proposal_title ILIKE ${n} OR p.submitting_entity_name ILIKE ${n})"
        )
    cols = ", ".join(f"p.{c}" for c in PROPOSAL_COLUMNS)
    sql = f"SELECT {cols}, COUNT(*) OVER () AS _total FROM proposals p"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    params.append(effective_limit)
    sql += f" ORDER BY p.proposal_number LIMIT ${len(params)}"
    return sql, params


def build_aggregate_sql(group_by: list[str], filters: dict) -> tuple[str, list, bool]:
    unknown = [g for g in group_by if g not in AGG_PROPOSAL_FIELDS | AGG_PERSON_FIELDS]
    if unknown:
        raise ToolError(
            "invalid_args",
            f"unknown group_by field(s): {', '.join(repr(g) for g in unknown)}",
        )
    overlapping = any(g in AGG_PERSON_FIELDS for g in group_by)
    exprs = [
        f"pp.{g}" if g in AGG_PERSON_FIELDS else f"p.{g}" for g in group_by
    ]
    params: list = []
    # Filters stay as =/EXISTS on p so they never restrict the grouping join.
    clauses = _filter_clauses(filters, params)
    sql = "SELECT "
    sql += ", ".join(f"{expr} AS g{i}" for i, expr in enumerate(exprs))
    sql += ", COUNT(DISTINCT p.proposal_number) AS count FROM proposals p"
    if overlapping:
        sql += " JOIN proposal_personnel pp ON pp.proposal_number = p.proposal_number"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    group_cols = ", ".join(f"g{i}" for i in range(len(exprs)))
    sql += f" GROUP BY {group_cols} ORDER BY count DESC, {group_cols}"
    return sql, params, overlapping


async def search_proposals(pool, args: dict, settings) -> dict:
    limit = _clamp_limit(args.get("limit"), settings.mcp_max_rows)
    sql, params = build_search_proposals_sql(
        args.get("filters") or {}, args.get("keywords"), limit
    )
    rows = await pool.fetch(sql, *params)
    total = rows[0]["_total"] if rows else 0
    data = [_row_dict(r, PROPOSAL_COLUMNS) for r in rows]
    return envelope(
        data, total, max_rows=settings.mcp_max_rows, max_chars=settings.mcp_max_text_chars
    )


async def get_proposal(pool, args: dict, settings) -> dict:
    number = args["proposal_number"]
    cols = ", ".join(PROPOSAL_COLUMNS)
    row = await pool.fetchrow(
        f"SELECT {cols} FROM proposals WHERE proposal_number = $1", number
    )
    if row is None:
        raise ToolError("not_found", f"unknown proposal_number {number!r}")
    personnel = await pool.fetch(
        "SELECT pp.person_orcid, pe.first_name, pe.middle_name, pe.last_name,"
        " pp.proposal_role, pp.affiliation_uei, pp.affiliation_name,"
        " pp.factor1_assessment, pp.factor2_assessment, pp.factor3_assessment,"
        " pp.factor4_assessment, pp.person_overall_assessment,"
        " pp.multiple_mitigation, pp.mitigation_explanation_person"
        " FROM proposal_personnel pp"
        " JOIN personnel pe ON pe.person_orcid = pp.person_orcid"
        " WHERE pp.proposal_number = $1 ORDER BY pp.person_orcid",
        number,
    )
    file_refs = await pool.fetch(
        "SELECT scope, person_orcid, filename, metadata FROM file_refs"
        " WHERE proposal_number = $1 ORDER BY id",
        number,
    )
    data = _row_dict(row, PROPOSAL_COLUMNS)
    data["personnel"] = [_row_dict(r, PERSONNEL_COLUMNS) for r in personnel]
    data["file_refs"] = [
        {
            "scope": r["scope"],
            "person_orcid": r["person_orcid"],
            "filename": r["filename"],
            "metadata": _jsonb(r["metadata"]),
        }
        for r in file_refs
    ]
    data, text_truncated = truncate_strings(data, settings.mcp_max_text_chars)
    return {"data": data, "meta": {"total": 1, "returned": 1, "truncated": text_truncated}}


async def search_personnel(pool, args: dict, settings) -> dict:
    name, orcid, affiliation = args.get("name"), args.get("orcid"), args.get("affiliation")
    if not (name or orcid or affiliation):
        raise ToolError(
            "invalid_args", "provide at least one of name, orcid, or affiliation"
        )
    limit = _clamp_limit(args.get("limit"), settings.mcp_max_rows)
    params: list = []
    clauses = []
    join = ""
    if name:
        params.append(f"%{name}%")
        n = len(params)
        clauses.append(
            f"(pe.first_name ILIKE ${n} OR pe.middle_name ILIKE ${n}"
            f" OR pe.last_name ILIKE ${n})"
        )
    if orcid:
        params.append(orcid)
        clauses.append(f"pe.person_orcid = ${len(params)}")
    if affiliation:
        # Affiliations live per proposal appearance, hence the join.
        join = " JOIN proposal_personnel pp ON pp.person_orcid = pe.person_orcid"
        params.append(f"%{affiliation}%")
        clauses.append(f"pp.affiliation_name ILIKE ${len(params)}")
    params.append(limit)
    # DISTINCT inside the subquery, window count outside: window functions
    # would otherwise count pre-DISTINCT join rows.
    sql = (
        "SELECT q.*, COUNT(*) OVER () AS _total FROM ("
        "SELECT DISTINCT pe.person_orcid, pe.first_name, pe.middle_name, pe.last_name"
        f" FROM personnel pe{join} WHERE " + " AND ".join(clauses) + ") q"
        f" ORDER BY q.person_orcid LIMIT ${len(params)}"
    )
    rows = await pool.fetch(sql, *params)
    total = rows[0]["_total"] if rows else 0
    people = {
        r["person_orcid"]: {
            "person_orcid": r["person_orcid"],
            "first_name": r["first_name"],
            "middle_name": r["middle_name"],
            "last_name": r["last_name"],
            "proposals": [],
        }
        for r in rows
    }
    if people:
        proposal_rows = await pool.fetch(
            "SELECT pp.person_orcid, pp.proposal_number, pr.proposal_title,"
            " pp.proposal_role, pp.person_overall_assessment"
            " FROM proposal_personnel pp"
            " JOIN proposals pr ON pr.proposal_number = pp.proposal_number"
            " WHERE pp.person_orcid = ANY($1::text[])"
            " ORDER BY pp.person_orcid, pp.proposal_number",
            list(people),
        )
        for r in proposal_rows:
            people[r["person_orcid"]]["proposals"].append(
                {
                    "proposal_number": r["proposal_number"],
                    "proposal_title": r["proposal_title"],
                    "proposal_role": r["proposal_role"],
                    "person_overall_assessment": r["person_overall_assessment"],
                }
            )
    return envelope(
        list(people.values()),
        total,
        max_rows=settings.mcp_max_rows,
        max_chars=settings.mcp_max_text_chars,
    )


async def aggregate_assessments(pool, args: dict, settings) -> dict:
    group_by = args["group_by"]
    sql, params, overlapping = build_aggregate_sql(group_by, args.get("filters") or {})
    rows = await pool.fetch(sql, *params)
    data = [
        {
            "group": {field: row[f"g{i}"] for i, field in enumerate(group_by)},
            "count": row["count"],
        }
        for row in rows
    ]
    return envelope(
        data,
        len(data),
        max_rows=settings.mcp_max_rows,
        max_chars=settings.mcp_max_text_chars,
        extra_meta={"overlapping_buckets": overlapping},
    )


def _jsonb(value):
    """asyncpg returns jsonb as str unless a codec is registered."""
    if isinstance(value, str):
        return json.loads(value)
    return value if value is not None else {}
