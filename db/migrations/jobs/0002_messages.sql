-- phase-9: stateless multi-turn. Persist the client-supplied conversation
-- (jsonb array of {role, content}) for audit/recovery; NULL for single-turn
-- jobs. Retention: swept with the job row (same DELETE, no cascade needed).
ALTER TABLE jobs ADD COLUMN messages jsonb;
