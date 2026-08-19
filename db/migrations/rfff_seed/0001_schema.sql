-- rfff_seed initial schema (phase 2). Shape: docs/specs/data-model.md.
-- Enum-ish columns carry NO CHECK constraints on purpose: the seed pipeline
-- loads observed values as found (flag, don't drop) and records violations in
-- quarantine; the observed value sets live in observed_enums.

CREATE TABLE proposals (
    proposal_number              text PRIMARY KEY,
    fiscal_year                  text,
    ssa                          text,
    opportunity_number           text,
    proposal_title               text,
    submitting_entity_uei        text,
    submitting_entity_name       text,
    review_type                  text,
    reviewing_component          text,
    reviewing_subcomponent       text,
    assessment_state             text,
    approved_date                date,
    review_notes                 text,
    mitigation_status            text,
    mitigation_strategy_proposal text[],
    mitigation_explanation       text,
    award_type                   text,
    award_state                  text,
    fain                         text,
    awarded_date                 date,
    award_pop_start              date,
    award_pop_end                date
);

CREATE TABLE personnel (
    person_orcid text PRIMARY KEY,
    first_name   text,
    middle_name  text,
    last_name    text
);

CREATE TABLE proposal_personnel (
    proposal_number               text NOT NULL REFERENCES proposals (proposal_number),
    person_orcid                  text NOT NULL REFERENCES personnel (person_orcid),
    proposal_role                 text,
    affiliation_uei               text,
    affiliation_name              text,
    factor1_assessment            text,
    factor2_assessment            text,
    factor3_assessment            text,
    factor4_assessment            text,
    person_overall_assessment     text,
    multiple_mitigation           text[],
    mitigation_explanation_person text,
    PRIMARY KEY (proposal_number, person_orcid)
);

COMMENT ON COLUMN proposal_personnel.person_overall_assessment IS
    'Opaque stored value (architectural invariant 9): match/display verbatim only; upstream formula unknown; never compute, derive, or explain it.';

-- Person-centric queries are first-class (people recur across ~10 proposals).
CREATE INDEX proposal_personnel_person_orcid_idx
    ON proposal_personnel (person_orcid);

-- File POINTERS only, never blobs. person-scoped refs are owned by a
-- (proposal_number, person_orcid) pair; proposal/review refs by the proposal.
CREATE TABLE file_refs (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    scope           text NOT NULL CHECK (scope IN ('proposal', 'person', 'review')),
    proposal_number text NOT NULL REFERENCES proposals (proposal_number),
    person_orcid    text REFERENCES personnel (person_orcid),
    filename        text NOT NULL,
    metadata        jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK (scope <> 'person' OR person_orcid IS NOT NULL)
);

-- The RFFF data dictionary, keyed by CSV field name (as are observed_enums).
CREATE TABLE field_dictionary (
    field_name        text PRIMARY KEY,
    requirement       text,
    data_type         text,
    description       text,
    dictionary_values text[]
);

-- Profiled observed values per enum-ish field; in_dictionary=false marks
-- values the dictionary does not document. Regenerated on every seed run.
CREATE TABLE observed_enums (
    field_name    text NOT NULL,
    value         text NOT NULL,
    row_count     integer NOT NULL,
    in_dictionary boolean NOT NULL,
    PRIMARY KEY (field_name, value)
);

-- Enforced singleton: the assembled planning-prompt context block
-- (dictionary descriptions + observed enums + data-quality caveats),
-- regenerated on every seed run.
CREATE TABLE planner_context (
    id           boolean PRIMARY KEY DEFAULT true CHECK (id),
    context      jsonb NOT NULL,
    generated_at timestamptz NOT NULL DEFAULT now()
);

-- Rows/values that failed validation but were loaded anyway (flag, don't
-- drop), or were structurally unusable.
CREATE TABLE quarantine (
    id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    row_ref text NOT NULL,
    rule    text NOT NULL,
    detail  text,
    at      timestamptz NOT NULL DEFAULT now()
);
