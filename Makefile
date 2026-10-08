PY := backend/.venv/bin
export BDO_CONFIG ?= $(CURDIR)/config/battery.yaml
export BDO_DATA_DIR ?= $(CURDIR)/data
SPEED ?= 720
DAY ?= 2025-10-14

.PHONY: setup test lint typecheck backtest forecast sweep dev e2e docs up

setup:            ## create the virtualenv and install both halves
	python3.12 -m venv backend/.venv
	$(PY)/pip install -e "backend[dev]"
	cd frontend && npm ci

test:             ## unit and integration tests with the 80 % coverage gate (NF-2)
	cd backend && .venv/bin/pytest -q --cov=bdo/optimizer --cov=bdo/device --cov-fail-under=80

lint:
	cd backend && .venv/bin/ruff check .
	cd frontend && npm run lint

typecheck:
	cd backend && .venv/bin/mypy bdo
	cd frontend && npm run typecheck

backtest:         ## one-year backtest, writes data/runs/<year>-<config hash>/
	$(PY)/bdo backtest --year 2025

forecast:         ## train the price forecaster and score it in EUR on 2025
	$(PY)/bdo forecast --year 2025

sweep:            ## 1h / 2h / 4h durations x three efficiencies
	$(PY)/bdo sweep --year 2025

dev:              ## simulator, controller, API and dashboard as local processes
	@trap 'kill 0' INT TERM; \
	$(PY)/bdo sim --speed $(SPEED) & \
	$(PY)/bdo control --speed $(SPEED) --day $(DAY) & \
	$(PY)/bdo serve & \
	(cd frontend && npx vite --host 127.0.0.1 --port 5173 --strictPort) & \
	wait

e2e:              ## browser tests against a running stack (make dev, or BDO_UI_URL=http://localhost:8080)
	cd frontend && npx playwright test

docs:             ## regenerate the register map from the code
	$(PY)/bdo registers > docs/register-map.md

up:
	docker compose up --build
