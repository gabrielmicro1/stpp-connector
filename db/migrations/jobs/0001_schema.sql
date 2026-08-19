-- jobs initial schema (phase 2). Shape: docs/specs/integration-api.md.
-- ON DELETE CASCADE throughout so the retention sweep (JOBS_RETENTION_HOURS)
-- is a single DELETE FROM jobs by age.

CREATE TABLE jobs (
    job_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_sub   text NOT NULL,
    query      text NOT NULL,
    status     text NOT NULL
        CHECK (status IN ('planning', 'executing', 'synthesizing', 'complete', 'failed')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX jobs_created_at_idx ON jobs (created_at);

CREATE TABLE job_plans (
    job_id       uuid PRIMARY KEY REFERENCES jobs (job_id) ON DELETE CASCADE,
    plan         jsonb NOT NULL,
    validated_at timestamptz
);

-- status includes 'pending' (not yet started) in addition to the SSE-visible
-- step statuses. result is size-capped before storage (same cap the MCP
-- server applies); a fanned-out step stores the ordered per-call result list.
CREATE TABLE job_steps (
    job_id      uuid NOT NULL REFERENCES jobs (job_id) ON DELETE CASCADE,
    step_id     integer NOT NULL,
    status      text NOT NULL
        CHECK (status IN ('pending', 'running', 'repairing', 'complete', 'failed')),
    tool        text NOT NULL,
    args        jsonb,
    result      jsonb,
    error       text,
    started_at  timestamptz,
    finished_at timestamptz,
    PRIMARY KEY (job_id, step_id)
);

-- Write-then-emit: the SSE emitter and GET /v1/jobs/{job_id} both read from
-- here, which is what keeps the stream and the polling path consistent.
CREATE TABLE job_events (
    job_id uuid NOT NULL REFERENCES jobs (job_id) ON DELETE CASCADE,
    seq    bigint NOT NULL,
    type   text NOT NULL,
    data   jsonb NOT NULL,
    at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (job_id, seq)
);
