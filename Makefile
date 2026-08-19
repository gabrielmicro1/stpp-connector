.PHONY: up seed test demo migrate check-contracts

up:
	docker compose up --build -d
	docker compose ps

# Seed rfff_seed from data/mock (phase 3). Hermetic: runs seed_rfff.py inside
# the integration-api image (DSN comes from the service's env, invariant 7).
# Phase 7 adds seed_wdp.py (wdp database) after this step.
seed: migrate
	docker compose run --rm \
		-v $(CURDIR)/scripts:/opt/stpp/scripts:ro \
		-v $(CURDIR)/data:/opt/stpp/data:ro \
		integration-api python /opt/stpp/scripts/seed_rfff.py \
		--data-dir /opt/stpp/data/mock
	@echo "seed_wdp.py: pending phase 7"

# Hermetic: tests run inside the service images; no host venv needed.
test:
	docker compose build integration-api mcp-server fake-wdp
	docker compose run --rm --no-deps integration-api pytest -q
	docker compose run --rm --no-deps mcp-server pytest -q
	docker compose run --rm --no-deps fake-wdp pytest -q
	docker compose run --rm --no-deps \
		-v $(CURDIR)/scripts:/opt/stpp/scripts:ro \
		-v $(CURDIR)/data:/opt/stpp/data:ro \
		integration-api pytest -q -p no:cacheprovider /opt/stpp/scripts/tests

# Stub until phase 8 (demo-script queries + JWTs need seed + mint_jwt.py).
demo:
	@echo "make demo: not implemented until phase 8 (needs seeded anchors + scripts/mint_jwt.py)"

# Apply SQL migrations to the three databases (starts postgres if needed).
# Hermetic: runs scripts/migrate.py inside the integration-api image.
migrate:
	docker compose build integration-api
	for db in rfff_seed jobs wdp; do \
		docker compose run --rm \
			-v $(CURDIR)/scripts:/opt/stpp/scripts:ro \
			-v $(CURDIR)/db:/opt/stpp/db:ro \
			integration-api python /opt/stpp/scripts/migrate.py \
			--dsn postgresql://stpp:stpp@postgres:5432/$$db \
			--dir /opt/stpp/db/migrations/$$db || exit 1; \
	done

# Validate the phase-2 contract artifacts (JSON Schemas + OpenAPI).
check-contracts:
	docker run --rm -v $(CURDIR):/w -w /w python:3.12-slim sh -c \
		"pip install -q jsonschema 'openapi-spec-validator>=0.7' pyyaml \
		&& python scripts/check_contracts.py"
