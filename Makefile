.PHONY: up seed test demo

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
