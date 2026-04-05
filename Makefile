# ─────────────────────────────────────────────────────────────────────────
# Smart PUC — Developer Makefile
# ─────────────────────────────────────────────────────────────────────────
# Cross-platform entrypoint for common development, build, test, and
# benchmark tasks. Works on Linux, macOS, and Windows (WSL / Git Bash).
#
# For a complete walkthrough see docs/REPRODUCIBILITY.md.

.DEFAULT_GOAL := help

PY       ?= python
PIP      ?= pip
NPX      ?= npx
NPM      ?= npm

GANACHE_FLAGS := --deterministic --accounts 10 --defaultBalanceEther 100 --port 7545 --gasLimit 12000000
HARDHAT_NODE_PORT ?= 7545

.PHONY: help
help: ## Show this help message
	@awk 'BEGIN {FS = ":.*##"; printf "\nSmart PUC — available targets\n\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

# ─── Installation ────────────────────────────────────────────────────────

.PHONY: install
install: install-node install-python ## Install both Node and Python dependencies

.PHONY: install-node
install-node: ## Install Node dependencies (Truffle, OpenZeppelin, Ganache)
	$(NPM) install

.PHONY: install-python
install-python: ## Install Python dependencies
	$(PIP) install -r requirements.txt

# ─── Smart contract build / test ─────────────────────────────────────────

.PHONY: compile
compile: ## Compile Solidity contracts (Hardhat) and flatten artifacts
	$(NPX) hardhat compile

.PHONY: deploy
deploy: ## Deploy UUPS proxies to the local Ganache / Hardhat node
	$(NPX) hardhat run scripts/deploy.js --network localhost

.PHONY: deploy-hardhat
deploy-hardhat: ## Deploy against the in-process Hardhat network
	$(NPX) hardhat run scripts/deploy.js --network hardhat

.PHONY: test-sol
test-sol: ## Run the Hardhat Solidity test suite (including UUPS upgrade tests)
	$(NPX) hardhat test

.PHONY: test-sol-gas
test-sol-gas: ## Run Hardhat tests with gas reporter enabled
	REPORT_GAS=true $(NPX) hardhat test

.PHONY: slither
slither: ## Run Slither static analysis (requires slither-analyzer)
	slither contracts/ --exclude-dependencies

# ─── Python tests / lint ─────────────────────────────────────────────────

.PHONY: test-py
test-py: ## Run the Python test suite with coverage
	$(PY) -m pytest tests/ -v --cov=backend --cov=ml --cov=physics --cov=integrations --cov-report=term-missing

.PHONY: lint
lint: ## Run flake8 over the Python sources
	$(PY) -m flake8 backend/ ml/ physics/ integrations/ obd_node/ --max-line-length=120 --max-complexity=15 --extend-ignore=E501,W503,E402

.PHONY: test
test: test-sol test-py ## Run all tests (Solidity + Python)

# ─── Run the stack ───────────────────────────────────────────────────────

.PHONY: ganache
ganache: ## Start a deterministic local Ganache (foreground)
	$(NPX) ganache $(GANACHE_FLAGS)

.PHONY: node
node: ## Start an in-process Hardhat node on :7545 (foreground)
	$(NPX) hardhat node --port $(HARDHAT_NODE_PORT)

.PHONY: backend
backend: ## Start the FastAPI testing-station backend (uvicorn)
	cd backend && $(PY) -m uvicorn main:app --host 0.0.0.0 --port 5000 --reload

.PHONY: frontend
frontend: ## Serve the frontend on http://localhost:3000
	$(NPX) http-server frontend -p 3000 -c-1 --cors

.PHONY: obd
obd: ## Start the OBD device simulator (10 vehicles, 3 s interval)
	$(PY) -m obd_node.obd_device --count 10 --interval 3

.PHONY: up
up: ## Bring up the full docker-compose stack
	docker compose up --build -d

.PHONY: down
down: ## Tear down the docker-compose stack
	docker compose down -v

.PHONY: logs
logs: ## Tail docker-compose logs
	docker compose logs -f

# ─── Benchmarks and paper artifacts ──────────────────────────────────────

.PHONY: bench-gas
bench-gas: ## Measure gas for every write op; writes docs/gas_report.json
	$(NPX) hardhat run scripts/measure_gas.js --network localhost

.PHONY: bench-latency
bench-latency: ## Run end-to-end latency benchmark (1000 samples)
	$(PY) scripts/bench_latency.py --samples 1000 --output docs/bench_latency.json

.PHONY: bench-throughput
bench-throughput: ## Sweep concurrency (1..32 workers) and measure TPS
	$(PY) scripts/bench_throughput.py --workers 1,4,8,16,32 --samples-per-worker 200 --output docs/bench_throughput.json

.PHONY: bench-fraud
bench-fraud: ## Generate labelled-attack dataset and evaluate the fraud detector
	$(PY) -m ml.fraud_evaluation --samples 5000 --output docs/fraud_eval_report.json

.PHONY: bench
bench: bench-gas bench-latency bench-throughput bench-fraud ## Run every benchmark in sequence

# ─── Housekeeping ────────────────────────────────────────────────────────

.PHONY: clean
clean: ## Remove build artifacts and caches
	rm -rf build/contracts/*.json
	rm -rf .pytest_cache htmlcov .coverage
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name node_modules -prune -o -type f -name "*.pyc" -exec rm -f {} + 2>/dev/null || true

.PHONY: format
format: ## Format Python sources with black (if installed)
	$(PY) -m black backend/ ml/ physics/ integrations/ obd_node/ scripts/ || true

.PHONY: sri
sri: ## Download CDN assets and inject SHA-384 SRI hashes into frontend HTML
	$(PY) scripts/compute_sri.py

.PHONY: env
env: ## Copy .env.example to .env (fail if .env already exists)
	@test ! -f .env || (echo "Refusing to overwrite existing .env"; exit 1)
	cp .env.example .env
	@echo "Created .env — open it and fill in the required values."
