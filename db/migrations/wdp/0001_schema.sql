-- wdp initial schema (phase 2): synthetic research-world data served by
-- fake-wdp. persons/entities are the ref_id dimension tables; one polymorphic
-- documents table backs GET /v1/documents/{ref_id}. (Decision recorded in
-- docs/specs/data-model.md; publication/funding counts in the /v1/persons and
-- /v1/entities summaries derive from documents.)

CREATE TABLE persons (
    ref_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    orcid        text NOT NULL UNIQUE,
    name         text NOT NULL,
    affiliations text[] NOT NULL DEFAULT '{}'
);

CREATE TABLE entities (
    ref_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    uei     text NOT NULL UNIQUE,
    name    text NOT NULL,
    country text
);

CREATE TABLE documents (
    doc_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- persons.ref_id or entities.ref_id; polymorphic, so indexed but no FK.
    ref_id uuid NOT NULL,
    type   text NOT NULL
        CHECK (type IN ('publication', 'funding_record', 'entity_record')),
    title  text NOT NULL,
    year   integer,
    source text,
    detail jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX documents_ref_id_idx ON documents (ref_id);

-- Written by scripts/seed_wdp.py after verifying FAKE_WDP_DENY_ORCIDS; read
-- by `make demo`. Keys: query2_proposal_number, query3b_person_name,
-- query3b_denied_orcid.
CREATE TABLE demo_anchors (
    key   text PRIMARY KEY,
    value text NOT NULL
);
