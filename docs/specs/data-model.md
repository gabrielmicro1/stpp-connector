# Spec: Data Model and Seed Pipeline

Inputs (committed at `data/mock/`):
- `proposal-assessment-schema.csv` — the RFFF data dictionary (field name,
  requirement, type, description, allowed values).
- `proposal-assessment-mock.csv` — 1,000 denormalized rows: one row per
  person-per-proposal (336 proposals × 1–5 personnel; 100 unique people who
  recur across ~10 proposals each).

## Normalized schema (db `rfff_seed`; DDL finalized in phase 2 migrations)

```
proposals            proposal_number PK; fiscal_year, ssa, opportunity_number,
                     proposal_title, submitting_entity_uei, submitting_entity_name,
                     review_type, reviewing_component, reviewing_subcomponent,
                     assessment_state, approved_date, review_notes,
                     mitigation_status, mitigation_strategy_proposal text[],
                     mitigation_explanation, award_type, award_state, fain,
                     awarded_date, award_pop_start, award_pop_end
personnel            person_orcid PK; first_name, middle_name, last_name
proposal_personnel   (proposal_number, person_orcid) PK; proposal_role,
                     affiliation_uei, affiliation_name,
                     factor1_assessment .. factor4_assessment,
                     person_overall_assessment,        -- OPAQUE (invariant 9)
                     multiple_mitigation text[], person mitigation fields
file_refs            id PK; scope (proposal|person|review), owner keys,
                     filename, metadata jsonb          -- pointers, not blobs
field_dictionary     field_name PK; requirement, data_type, description,
                     dictionary_values text[]
observed_enums       field_name, value, row_count, in_dictionary bool
planner_context      singleton jsonb — regenerated on each seed run; the
                     assembled planning-prompt context block
quarantine           row_ref, rule, detail — rows/values that failed validation
```

Mock-data verification confirmed the split is clean: proposal-level fields
are constant within each proposal_number; ORCIDs keep consistent names.

## Seed pipeline stages (`scripts/seed_rfff.py`; prod sync service inherits these)

1. **Ingest** both CSVs.
2. **Validate & profile** (report printed and stored):
   - Extract observed values per enum-ish field; diff against dictionary;
     write `observed_enums` with `in_dictionary` flags.
   - Referential checks (role/name consistency, duplicate keys).
   - Date sanity: pop_end ≥ pop_start; awarded_date ⇒ award_state='Awarded';
     approved_date year plausibility. Violations → `quarantine` with the row
     still loaded (flag, don't drop — demo data is known-messy) unless the
     row is structurally unusable.
3. **Normalize & load** into the tables above; split comma-joined multi-values
   (`mitigation_strategy_proposal`, `multiple_mitigation`) into text[].
4. **Generate planner context**: dictionary descriptions + observed enums
   (flagging undocumented values) + data-quality caveats + hole notes.

## Known data-quality findings (mock data; encode as caveats, verify upstream)

- Observed enum values NOT in the dictionary: proposal_role adds
  `Consultant`; assessment_state adds `Canceled`; mitigation_status adds
  `Complete`, `Pending`; award_state adds `Declined`, `Pending`;
  mitigation_strategy_proposal is secretly multi-valued.
- `person_overall_assessment` ≠ worst-of-factors (matches only ~33%);
  dictionary says "Calculated" without the formula → treat as opaque.
- PI coverage unreliable: 184/336 proposals have no PI; 38 have >1. Tools
  return roles as-found; the planner must not assume one PI per proposal.
- Dates untrustworthy: 252 rows pop_end < pop_start; 470 rows have
  awarded_date with award_state ≠ 'Awarded'; approved years 2020–2025 vs FY
  enum 2023–2026. Planner context: do not build reasoning on date consistency.
- `ssa` allowed values are "Not specified in Lists tab" in the dictionary —
  open question for STPP; planner uses observed values meanwhile.
- Clean: zero missing required fields; FAIN appears only on Awarded rows.

These caveats ship INTO the planner context so answers stay honest (e.g.
aggregations show 'Complete' and 'Implemented' as distinct buckets).

## Sensitivity

Records tie named individuals (ORCIDs) to security risk assessments. Demo
consequences: per-user scoping applies to LOCAL tools too, audit covers local
reads, jobs retention short (see integration-api spec). Open authz question
for STPP: cross-component visibility rules.

## `scripts/seed_wdp.py` (db `wdp`)

Reads ORCIDs/UEIs from rfff_seed; generates deterministic (fixed-RNG-seed)
synthetic `publications`, `funding_records`, `entities` referencing them, a
few hundred rows, so cross-boundary demo queries actually join. A small
percentage of ORCIDs get NO WDP records (exercises not_found → repair path).

Also: verifies every ORCID in `FAKE_WDP_DENY_ORCIDS` exists in rfff_seed AND
receives WDP records (denial must differ from absence), then writes a
`demo_anchors` table — the Query-2 proposal_number (personnel include ≥1
ORCID with WDP records and ≥1 without) and the Query-3B person name +
denied ORCID. `make demo` reads `demo_anchors` to print fully substituted
demo queries.
