"""RFFF seed pipeline: ingest mock CSVs, validate-and-profile, normalize into
rfff_seed, and generate the planner context (docs/specs/data-model.md).

Usage: python scripts/seed_rfff.py [--dsn postgresql://...] [--data-dir data/mock]
--dsn defaults to $RFFF_SEED_DATABASE_URL. Stages: ingest -> validate & profile
(observed_enums, referential checks, date sanity, quarantine; flag don't drop)
-> normalize & load -> planner_context. Prints a human-readable profile report.
"""
import argparse
import asyncio
import csv
import datetime
import io
import json
import os
import pathlib
import sys
from collections import Counter
from dataclasses import dataclass, field

# The dictionary marks fields with no documented value list either way.
NO_LIST_MARKERS = {"N/A", "Not specified in Lists tab"}

# Dictionary data types treated as enum-ish (profiled into observed_enums).
ENUMISH_TYPES = {"Choice", "Multiple Values", "Calculated"}

# Comma-joined multi-value columns split into text[] on load.
MULTI_VALUE_FIELDS = {"mitigation_strategy_proposal", "multiple_mitigation"}

# CSV columns that must be constant within one proposal_number.
PROPOSAL_LEVEL_FIELDS = [
    "fiscal_year", "ssa", "opportunity_number", "proposal_title",
    "submitting_entity_uei", "submitting_entity_name", "review_type",
    "reviewing_component", "reviewing_subcomponent", "assessment_state",
    "approved_date", "review_notes", "mitigation_status",
    "mitigation_strategy_proposal", "mitigation_explanation_proposal",
    "award_type", "award_state", "fain", "awarded_date",
    "award_pop_start", "award_pop_end", "proposal_files", "review_files",
]

# Person identity: first+last. middle_name is Optional and jitters in the
# mock data, so variance there is a profile stat, not a quarantine rule.
PERSON_NAME_FIELDS = ["first_name", "last_name"]

DATE_FIELDS = ["approved_date", "awarded_date", "award_pop_start", "award_pop_end"]


@dataclass
class DictEntry:
    field_name: str
    description: str
    requirement: str
    data_type: str
    values: list[str] | None  # None: dictionary documents no value list


@dataclass
class Row:
    line: int  # 1-based CSV line number (header is line 1)
    data: dict[str, str]


@dataclass
class ObservedValue:
    value: str
    row_count: int
    in_dictionary: bool


@dataclass
class Quarantined:
    row_ref: str
    rule: str
    detail: str


@dataclass
class Profile:
    row_count: int = 0
    observed: dict[str, list[ObservedValue]] = field(default_factory=dict)
    fields_without_list: set[str] = field(default_factory=set)
    multi_valued_choice_fields: set[str] = field(default_factory=set)
    quarantine: list[Quarantined] = field(default_factory=list)
    proposals_without_pi: int = 0
    proposals_with_multiple_pi: int = 0
    personnel_with_variant_middle_name: int = 0


# --- ingest -----------------------------------------------------------------


def parse_dictionary(text: str) -> dict[str, DictEntry]:
    entries = {}
    for r in csv.DictReader(io.StringIO(text)):
        raw = (r.get("Possible Values") or "").strip()
        values = None
        if raw and raw not in NO_LIST_MARKERS:
            values = [v.strip() for v in raw.split(",") if v.strip()]
        entries[r["Field Name"]] = DictEntry(
            field_name=r["Field Name"],
            description=r.get("Description", ""),
            requirement=r.get("Requirement", ""),
            data_type=(r.get("Data Type") or "").strip(),
            values=values,
        )
    return entries


def parse_rows(text: str) -> list[Row]:
    return [
        Row(line=i, data=dict(r))
        for i, r in enumerate(csv.DictReader(io.StringIO(text)), start=2)
    ]


def parse_date(raw: str) -> datetime.date | None:
    """M/D/YYYY [H:MM] -> date; '' -> None; anything else raises ValueError."""
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in ("%m/%d/%Y %H:%M", "%m/%d/%Y"):
        try:
            return datetime.datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unparseable date: {raw!r}")


# --- validate & profile -----------------------------------------------------


def _row_values(entry: DictEntry, raw: str) -> list[str]:
    """Observed values one row contributes for an enum-ish field.

    Multiple-Values fields always comma-split. Choice fields with a documented
    list comma-split only when every part is a documented member (that is the
    'secretly multi-valued' case) — otherwise the raw value counts whole so an
    undocumented value containing a comma is not shredded.
    """
    raw = (raw or "").strip()
    if not raw:
        return []
    if entry.data_type == "Multiple Values":
        return [p.strip() for p in raw.split(",") if p.strip()]
    if entry.values is not None and raw not in entry.values and "," in raw:
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if len(parts) > 1 and all(p in entry.values for p in parts):
            return parts
    return [raw]


def _row_ref(row: Row) -> str:
    return (
        f"csv line {row.line} (proposal {row.data.get('proposal_number', '?')}, "
        f"orcid {row.data.get('person_orcid', '?')})"
    )


def _safe_date(row: Row, fld: str, prof: Profile) -> datetime.date | None:
    try:
        return parse_date(row.data.get(fld, ""))
    except ValueError:
        prof.quarantine.append(
            Quarantined(
                row_ref=_row_ref(row),
                rule="unparseable_date",
                detail=f"{fld}={row.data.get(fld)!r}",
            )
        )
        return None


def profile(rows: list[Row], dictionary: dict[str, DictEntry]) -> Profile:
    prof = Profile(row_count=len(rows))

    # Observed enums (raw rows, before dedup: this profiles the data as found).
    enum_fields = [
        f for f, e in dictionary.items()
        if e.data_type in ENUMISH_TYPES and rows and f in rows[0].data
    ]
    counts: dict[str, Counter] = {f: Counter() for f in enum_fields}
    for row in rows:
        for f in enum_fields:
            entry = dictionary[f]
            values = _row_values(entry, row.data.get(f, ""))
            if (
                entry.data_type == "Choice"
                and entry.values is not None
                and len(values) > 1
            ):
                prof.multi_valued_choice_fields.add(f)
            counts[f].update(values)
    for f in enum_fields:
        entry = dictionary[f]
        if entry.values is None:
            prof.fields_without_list.add(f)
        prof.observed[f] = [
            ObservedValue(
                value=v,
                row_count=n,
                in_dictionary=entry.values is not None and v in entry.values,
            )
            for v, n in sorted(counts[f].items())
        ]

    # Referential checks: first occurrence wins throughout (matches normalize).
    seen_keys: set[tuple[str, str]] = set()
    proposal_first: dict[str, Row] = {}
    person_first: dict[str, Row] = {}
    junction_roles: dict[tuple[str, str], str] = {}
    middle_names: dict[str, set[str]] = {}
    for row in rows:
        pn = row.data.get("proposal_number", "")
        orcid = row.data.get("person_orcid", "")
        key = (pn, orcid)
        if key in seen_keys:
            prof.quarantine.append(
                Quarantined(
                    row_ref=_row_ref(row),
                    rule="duplicate_key",
                    detail=f"duplicate (proposal_number, person_orcid)={key}; first occurrence kept",
                )
            )
        else:
            seen_keys.add(key)
            junction_roles[key] = (row.data.get("proposal_role") or "").strip()
        first = proposal_first.setdefault(pn, row)
        if first is not row:
            for f in PROPOSAL_LEVEL_FIELDS:
                if row.data.get(f, "") != first.data.get(f, ""):
                    prof.quarantine.append(
                        Quarantined(
                            row_ref=_row_ref(row),
                            rule="proposal_field_conflict",
                            detail=(
                                f"{f}: {row.data.get(f)!r} != first-seen "
                                f"{first.data.get(f)!r} (csv line {first.line})"
                            ),
                        )
                    )
        middle_names.setdefault(orcid, set()).add(row.data.get("middle_name", ""))
        pfirst = person_first.setdefault(orcid, row)
        if pfirst is not row:
            for f in PERSON_NAME_FIELDS:
                if row.data.get(f, "") != pfirst.data.get(f, ""):
                    prof.quarantine.append(
                        Quarantined(
                            row_ref=_row_ref(row),
                            rule="person_name_conflict",
                            detail=(
                                f"{f}: {row.data.get(f)!r} != first-seen "
                                f"{pfirst.data.get(f)!r} (csv line {pfirst.line})"
                            ),
                        )
                    )

    # PI coverage stats (deduped junction rows; caveat material, not errors).
    pi_counts: Counter = Counter()
    for (pn, _orcid), role in junction_roles.items():
        pi_counts[pn] += role == "PI"
    prof.proposals_without_pi = sum(1 for n in pi_counts.values() if n == 0)
    prof.proposals_with_multiple_pi = sum(1 for n in pi_counts.values() if n > 1)
    prof.personnel_with_variant_middle_name = sum(
        1 for names in middle_names.values() if len(names) > 1
    )

    # Date sanity, per row (spec counts violations in rows). Flag, don't drop.
    fy_entry = dictionary.get("fiscal_year")
    fy_years = sorted(int(v) for v in fy_entry.values) if fy_entry and fy_entry.values else None
    for row in rows:
        dates = {f: _safe_date(row, f, prof) for f in DATE_FIELDS}
        start, end = dates["award_pop_start"], dates["award_pop_end"]
        if start and end and end < start:
            prof.quarantine.append(
                Quarantined(
                    row_ref=_row_ref(row),
                    rule="pop_end_before_pop_start",
                    detail=f"award_pop_end {end} < award_pop_start {start}",
                )
            )
        state = (row.data.get("award_state") or "").strip()
        if dates["awarded_date"] and state != "Awarded":
            prof.quarantine.append(
                Quarantined(
                    row_ref=_row_ref(row),
                    rule="awarded_date_without_awarded_state",
                    detail=f"awarded_date {dates['awarded_date']} but award_state={state!r}",
                )
            )
        approved = dates["approved_date"]
        if approved and fy_years and not fy_years[0] <= approved.year <= fy_years[-1]:
            prof.quarantine.append(
                Quarantined(
                    row_ref=_row_ref(row),
                    rule="approved_date_year_implausible",
                    detail=(
                        f"approved_date year {approved.year} outside dictionary "
                        f"fiscal-year span {fy_years[0]}-{fy_years[-1]}"
                    ),
                )
            )
    return prof


# --- normalize ----------------------------------------------------------------

PROPOSAL_TEXT_FIELDS = [
    "fiscal_year", "ssa", "opportunity_number", "proposal_title",
    "submitting_entity_uei", "submitting_entity_name", "review_type",
    "reviewing_component", "reviewing_subcomponent", "assessment_state",
    "review_notes", "mitigation_status", "award_type", "award_state", "fain",
]

JUNCTION_TEXT_FIELDS = [
    "proposal_role", "affiliation_uei", "affiliation_name",
    "factor1_assessment", "factor2_assessment", "factor3_assessment",
    "factor4_assessment", "person_overall_assessment",
    "mitigation_explanation_person",
]

FILE_COLUMNS = {  # CSV column -> file_refs.scope
    "proposal_files": "proposal",
    "person_files": "person",
    "review_files": "review",
}


@dataclass
class Normalized:
    proposals: list[dict]
    personnel: list[dict]
    proposal_personnel: list[dict]
    file_refs: list[dict]


def _text(row: Row, fld: str) -> str | None:
    value = (row.data.get(fld) or "").strip()
    return value or None


def _split(row: Row, fld: str) -> list[str] | None:
    parts = [p.strip() for p in (row.data.get(fld) or "").split(",") if p.strip()]
    return parts or None


def _date_or_none(row: Row, fld: str) -> datetime.date | None:
    try:
        return parse_date(row.data.get(fld, ""))
    except ValueError:  # profile() already quarantined it
        return None


def normalize(rows: list[Row]) -> Normalized:
    """Denormalized rows -> table-shaped records. First occurrence wins on
    every key (matching profile(), which quarantines the conflicts)."""
    proposals: dict[str, dict] = {}
    personnel: dict[str, dict] = {}
    junction: dict[tuple[str, str], dict] = {}
    file_refs: list[dict] = []
    seen_files: set[tuple] = set()

    for row in rows:
        pn = (row.data.get("proposal_number") or "").strip()
        orcid = (row.data.get("person_orcid") or "").strip()

        if pn not in proposals:
            rec = {"proposal_number": pn}
            rec.update({f: _text(row, f) for f in PROPOSAL_TEXT_FIELDS})
            rec["mitigation_strategy_proposal"] = _split(row, "mitigation_strategy_proposal")
            rec["mitigation_explanation"] = _text(row, "mitigation_explanation_proposal")
            rec.update({f: _date_or_none(row, f) for f in DATE_FIELDS})
            proposals[pn] = rec

        if orcid not in personnel:
            personnel[orcid] = {
                "person_orcid": orcid,
                "first_name": _text(row, "first_name"),
                "middle_name": _text(row, "middle_name"),
                "last_name": _text(row, "last_name"),
            }

        key = (pn, orcid)
        if key not in junction:
            rec = {"proposal_number": pn, "person_orcid": orcid}
            rec.update({f: _text(row, f) for f in JUNCTION_TEXT_FIELDS})
            rec["multiple_mitigation"] = _split(row, "multiple_mitigation")
            junction[key] = rec

        for column, scope in FILE_COLUMNS.items():
            filename = _text(row, column)
            if not filename:
                continue
            owner_orcid = orcid if scope == "person" else None
            dedup_key = (scope, pn, owner_orcid, filename)
            if dedup_key in seen_files:
                continue
            seen_files.add(dedup_key)
            file_refs.append(
                {
                    "scope": scope,
                    "proposal_number": pn,
                    "person_orcid": owner_orcid,
                    "filename": filename,
                    "metadata": {"source_column": column},
                }
            )

    return Normalized(
        proposals=list(proposals.values()),
        personnel=list(personnel.values()),
        proposal_personnel=list(junction.values()),
        file_refs=file_refs,
    )


# --- planner context ----------------------------------------------------------


def _quarantine_by_rule(prof: Profile) -> dict[str, int]:
    counts: Counter = Counter(q.rule for q in prof.quarantine)
    return dict(sorted(counts.items()))


def build_planner_context(dictionary: dict[str, DictEntry], prof: Profile) -> dict:
    """Assemble the planning-prompt context block: dictionary descriptions
    merged with OBSERVED enum values (invariant 10 — never the dictionary
    alone), plus generated data-quality caveats and profile stats."""
    fields = {}
    for name, entry in dictionary.items():
        fields[name] = {
            "description": entry.description,
            "requirement": entry.requirement,
            "data_type": entry.data_type,
            "dictionary_values": entry.values,
            "observed_values": (
                [
                    {"value": o.value, "row_count": o.row_count,
                     "in_dictionary": o.in_dictionary}
                    for o in prof.observed[name]
                ]
                if name in prof.observed
                else None
            ),
        }

    caveats = []
    undocumented = {
        f: [o.value for o in values if not o.in_dictionary]
        for f, values in prof.observed.items()
        if f not in prof.fields_without_list
    }
    undocumented = {f: v for f, v in undocumented.items() if v}
    for f, vals in sorted(undocumented.items()):
        caveats.append(
            f"{f}: observed value(s) not documented in the dictionary: "
            f"{', '.join(sorted(vals))}. Treat documented and undocumented "
            f"values as distinct buckets in filters and aggregations "
            f"(e.g. mitigation_status 'Complete' and 'Implemented' are "
            f"different observed states)."
        )
    if "person_overall_assessment" in dictionary:
        caveats.append(
            "person_overall_assessment is an opaque stored value: the upstream "
            "formula is unknown ('Calculated'), and it does NOT equal "
            "worst-of-factors. Match or display it verbatim; never compute, "
            "derive, or explain it."
        )
    for f in sorted(prof.multi_valued_choice_fields):
        caveats.append(
            f"{f} is dictionary-typed Choice but is observed multi-valued "
            f"(comma-joined); it is stored split into an array."
        )
    for f in sorted(prof.fields_without_list):
        if f in prof.multi_valued_choice_fields:
            continue
        note = (
            "open question for STPP; use the observed values meanwhile"
            if f == "ssa"
            else "use the observed values"
        )
        caveats.append(
            f"{f}: the dictionary documents no value list ({note}); observed "
            f"values are not data errors."
        )
    date_rules = {
        "pop_end_before_pop_start",
        "awarded_date_without_awarded_state",
        "approved_date_year_implausible",
        "unparseable_date",
    }
    by_rule = _quarantine_by_rule(prof)
    date_counts = {r: n for r, n in by_rule.items() if r in date_rules}
    if date_counts:
        summary = "; ".join(f"{r}={n}" for r, n in sorted(date_counts.items()))
        caveats.append(
            f"Dates are untrustworthy in this data ({summary} across "
            f"{prof.row_count} rows): do not build reasoning on date "
            f"consistency."
        )
    if prof.proposals_without_pi or prof.proposals_with_multiple_pi:
        caveats.append(
            f"PI coverage is unreliable: {prof.proposals_without_pi} proposals "
            f"have no PI and {prof.proposals_with_multiple_pi} have more than "
            f"one. Tools return roles as-found; never assume one PI per "
            f"proposal."
        )

    if prof.personnel_with_variant_middle_name:
        caveats.append(
            f"middle_name is inconsistent for "
            f"{prof.personnel_with_variant_middle_name} people (first and last "
            f"names are consistent per ORCID); the stored middle name is the "
            f"first seen — do not match people on middle name."
        )
    return {
        "fields": fields,
        "caveats": caveats,
        "profile": {
            "row_count": prof.row_count,
            "quarantine_by_rule": by_rule,
            "proposals_without_pi": prof.proposals_without_pi,
            "proposals_with_multiple_pi": prof.proposals_with_multiple_pi,
            "personnel_with_variant_middle_name": prof.personnel_with_variant_middle_name,
            "fields_without_list": sorted(prof.fields_without_list),
            "multi_valued_choice_fields": sorted(prof.multi_valued_choice_fields),
        },
    }


# --- report -------------------------------------------------------------------


def render_report(
    prof: Profile,
    dictionary: dict[str, DictEntry],
    load_counts: dict[str, int],
) -> str:
    lines = [
        "RFFF seed profile report",
        "========================",
        f"ingested rows: {prof.row_count}",
        "",
        "observed enum values",
        "--------------------",
    ]
    for f in sorted(prof.observed):
        silent = f in prof.fields_without_list
        suffix = "  (dictionary has no value list)" if silent else ""
        lines.append(f"{f}{suffix}")
        for o in prof.observed[f]:
            flag = "" if silent or o.in_dictionary else "  << UNDOCUMENTED"
            lines.append(f"  {o.value:<35} {o.row_count:>5}{flag}")
    if prof.multi_valued_choice_fields:
        lines += [
            "",
            "multi-valued Choice fields (comma-joined; stored as arrays): "
            + ", ".join(sorted(prof.multi_valued_choice_fields)),
        ]
    lines += [
        "",
        "referential / coverage",
        "----------------------",
        f"proposals without a PI:      {prof.proposals_without_pi}",
        f"proposals with multiple PIs: {prof.proposals_with_multiple_pi}",
        f"personnel with inconsistent middle names: {prof.personnel_with_variant_middle_name} (first+last consistent; stored first-seen)",
        "",
        "quarantine (flagged, still loaded unless structurally unusable)",
        "---------------------------------------------------------------",
    ]
    by_rule = _quarantine_by_rule(prof)
    if by_rule:
        lines += [f"  {rule:<40} {n:>5}" for rule, n in by_rule.items()]
    else:
        lines.append("  (none)")
    lines += ["", "loaded", "------"]
    lines += [f"  {table:<20} {n:>5}" for table, n in load_counts.items()]
    return "\n".join(lines)


# --- load (thin; exercised by `make seed`, not unit tests) ---------------------

PROPOSAL_COLUMNS = [
    "proposal_number", "fiscal_year", "ssa", "opportunity_number",
    "proposal_title", "submitting_entity_uei", "submitting_entity_name",
    "review_type", "reviewing_component", "reviewing_subcomponent",
    "assessment_state", "approved_date", "review_notes", "mitigation_status",
    "mitigation_strategy_proposal", "mitigation_explanation", "award_type",
    "award_state", "fain", "awarded_date", "award_pop_start", "award_pop_end",
]

PERSONNEL_COLUMNS = ["person_orcid", "first_name", "middle_name", "last_name"]

JUNCTION_COLUMNS = [
    "proposal_number", "person_orcid", "proposal_role", "affiliation_uei",
    "affiliation_name", "factor1_assessment", "factor2_assessment",
    "factor3_assessment", "factor4_assessment", "person_overall_assessment",
    "multiple_mitigation", "mitigation_explanation_person",
]


def _insert_sql(table: str, columns: list[str]) -> str:
    placeholders = ", ".join(f"${i}" for i in range(1, len(columns) + 1))
    return f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"


async def load(
    dsn: str,
    dictionary: dict[str, DictEntry],
    prof: Profile,
    norm: Normalized,
    context: dict,
) -> dict[str, int]:
    import asyncpg  # deferred so the pure stages stay importable without it

    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            # Idempotent re-seed: children first (planner_context is upserted).
            for table in ("file_refs", "proposal_personnel", "personnel",
                          "proposals", "field_dictionary", "observed_enums",
                          "quarantine"):
                await conn.execute(f"DELETE FROM {table}")

            await conn.executemany(
                _insert_sql("proposals", PROPOSAL_COLUMNS),
                [tuple(p[c] for c in PROPOSAL_COLUMNS) for p in norm.proposals],
            )
            await conn.executemany(
                _insert_sql("personnel", PERSONNEL_COLUMNS),
                [tuple(p[c] for c in PERSONNEL_COLUMNS) for p in norm.personnel],
            )
            await conn.executemany(
                _insert_sql("proposal_personnel", JUNCTION_COLUMNS),
                [tuple(p[c] for c in JUNCTION_COLUMNS)
                 for p in norm.proposal_personnel],
            )
            await conn.executemany(
                "INSERT INTO file_refs (scope, proposal_number, person_orcid, "
                "filename, metadata) VALUES ($1, $2, $3, $4, $5::jsonb)",
                [(r["scope"], r["proposal_number"], r["person_orcid"],
                  r["filename"], json.dumps(r["metadata"]))
                 for r in norm.file_refs],
            )
            await conn.executemany(
                "INSERT INTO field_dictionary (field_name, requirement, "
                "data_type, description, dictionary_values) "
                "VALUES ($1, $2, $3, $4, $5)",
                [(e.field_name, e.requirement, e.data_type, e.description,
                  e.values)
                 for e in dictionary.values()],
            )
            await conn.executemany(
                "INSERT INTO observed_enums (field_name, value, row_count, "
                "in_dictionary) VALUES ($1, $2, $3, $4)",
                [(f, o.value, o.row_count, o.in_dictionary)
                 for f, values in prof.observed.items() for o in values],
            )
            await conn.executemany(
                "INSERT INTO quarantine (row_ref, rule, detail) "
                "VALUES ($1, $2, $3)",
                [(q.row_ref, q.rule, q.detail) for q in prof.quarantine],
            )
            await conn.execute(
                "INSERT INTO planner_context (id, context, generated_at) "
                "VALUES (true, $1::jsonb, now()) "
                "ON CONFLICT (id) DO UPDATE "
                "SET context = EXCLUDED.context, generated_at = now()",
                json.dumps(context),
            )
    finally:
        await conn.close()
    return {
        "proposals": len(norm.proposals),
        "personnel": len(norm.personnel),
        "proposal_personnel": len(norm.proposal_personnel),
        "file_refs": len(norm.file_refs),
        "field_dictionary": len(dictionary),
        "observed_enums": sum(len(v) for v in prof.observed.values()),
        "quarantine": len(prof.quarantine),
        "planner_context": 1,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dsn",
        default=os.environ.get("RFFF_SEED_DATABASE_URL"),
        help="rfff_seed DSN (default: $RFFF_SEED_DATABASE_URL)",
    )
    parser.add_argument("--data-dir", default="data/mock", type=pathlib.Path)
    args = parser.parse_args()
    if not args.dsn:
        sys.exit("no DSN: pass --dsn or set RFFF_SEED_DATABASE_URL")

    dictionary = parse_dictionary(
        (args.data_dir / "proposal-assessment-schema.csv").read_text())
    rows = parse_rows(
        (args.data_dir / "proposal-assessment-mock.csv").read_text())
    prof = profile(rows, dictionary)
    norm = normalize(rows)
    context = build_planner_context(dictionary, prof)
    load_counts = asyncio.run(load(args.dsn, dictionary, prof, norm, context))
    print(render_report(prof, dictionary, load_counts))
    print("\nplanner_context regenerated "
          f"({len(context['caveats'])} caveats).")


if __name__ == "__main__":
    main()
