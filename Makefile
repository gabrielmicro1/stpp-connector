.PHONY: up seed test demo migrate check-contracts

up:
	docker compose up --build -d
	docker compose ps

# Stub until phase 3 (seed_rfff.py) and phase 7 (seed_wdp.py).
seed:
	@echo "make seed: not implemented until phase 3 (seed_rfff.py) / phase 7 (seed_wdp.py)"

# Hermetic: tests run inside the service images; no host venv needed.
test:
	docker compose build integration-api mcp-server fake-wdp
	docker compose run --rm --no-deps integration-api pytest -q
	docker compose run --rm --no-deps mcp-server pytest -q
	docker compose run --rm --no-deps fake-wdp pytest -q

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
