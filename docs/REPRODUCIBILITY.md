# Smart PUC — Reproducibility Guide

This document provides the exact, deterministic steps required to reproduce
every experimental claim made in the Smart PUC paper and in the `/docs`
folder. It exists to satisfy the Artifact Evaluation criteria of venues
such as IEEE Access, IEEE IoT Journal, and ACM TOIT.

## 1. Environment

| Component | Version | Notes |
|-----------|---------|-------|
| Node.js | 18.x or 20.x (LTS) | `node --version` |
| npm | 9+ | Shipped with Node |
| Python | 3.10 or 3.11 | `python --version` |
| Solidity compiler | 0.8.21 | Pinned in `hardhat.config.js` |
| OpenZeppelin contracts | 4.9.6 | Pinned in `package.json` |
| Web3.py | 6.15.1 | Pinned in `requirements.txt` |
| FastAPI | 0.115.0 | Pinned in `requirements.txt` |
| uvicorn | 0.30.6 | Pinned in `requirements.txt` |
| Pydantic | 2.9.2 | Pinned in `requirements.txt` |
| scikit-learn | 1.4.2 | Pinned in `requirements.txt` |
| Docker | 24.x or later | Optional — only for the one-command path |
| Git commit | `git rev-parse HEAD` | Record at the top of your paper's data availability section |

The exact commit hash used to produce the published results is recorded in
`docs/ARTIFACT_COMMIT.txt` (created by the release script). Reviewers should
check out that commit before reproducing.

## 2. One-Command Path (Docker)

The fastest way to reproduce the end-to-end system is via Docker Compose.
This path is deterministic (Ganache runs with `--deterministic`) and
requires no manual key configuration.

```bash
git clone https://github.com/your-org/Smart_PUC.git
cd Smart_PUC
git checkout <paper_tag>      # e.g. v3.1.0-artifact

docker-compose up --build -d
# Wait until all healthchecks pass (~30 seconds)
docker-compose ps

# Smoke test
curl http://localhost:5000/api/status
curl http://localhost:5000/api/simulate
```

Expected services:

| Service | Port | Healthcheck |
|---------|------|-------------|
| `ganache` | 7545 | HTTP JSON-RPC `eth_chainId` |
| `deploy-contracts` | — | One-shot, exits 0 |
| `station` (FastAPI backend) | 5000 | `GET /health` returns 200 |
| `obd-device` | — | Posts signed telemetry every 5 s |
| `frontend` | 3000 | HTTP 200 on `/index.html` |

## 3. Manual Path (without Docker)

```bash
# Deps
npm install
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Blockchain (terminal 1)
npx ganache --deterministic --accounts 10 --defaultBalanceEther 100 \
            --port 7545 --gasLimit 12000000

# Contracts (terminal 2)
npx hardhat compile && npx hardhat run scripts/deploy.js --network localhost

# Backend (terminal 3)
cp .env.example .env
# Fill in OBD_DEVICE_PRIVATE_KEY and PRIVATE_KEY from Ganache's printed accounts.
# Ganache in --deterministic mode prints the same addresses every run.
cd backend && python app.py

# Frontend (terminal 4)
npx http-server frontend -p 3000 -c-1 --cors

# OBD simulator (terminal 5)
python -m obd_node.obd_device --count 10 --interval 3
```

## 4. Reproducing the Published Measurements

Each `docs/*.md` file contains a **§ Reproducing These Numbers** section.
The one-liner for each:

```bash
# Gas cost table (docs/GAS_ANALYSIS.md)
npx hardhat run scripts/measure_gas.js --network localhost
# -> docs/gas_report.json

# End-to-end latency (docs/BENCHMARKS.md §2)
python scripts/bench_latency.py --samples 1000 \
    --station-url http://localhost:5000
# -> docs/bench_latency.json

# Throughput sweep (docs/BENCHMARKS.md §3)
python scripts/bench_throughput.py --workers 1,4,8,16,32 \
    --samples-per-worker 200
# -> docs/bench_throughput.json

# Fraud detector F1 (docs/FRAUD_EVALUATION.md)
python -m ml.fraud_evaluation --samples 5000
# -> docs/fraud_eval_report.json

# Offline scalability (benchmarks/)
python -m benchmarks.scalability_test
# -> prints LaTeX tables for the paper

# Hardhat tests (33+ contract tests incl. UUPS proxy semantics)
npx hardhat test

# Python tests (9 modules, with coverage)
python -m pytest tests/ -v --cov=backend --cov=ml --cov=physics --cov=integrations
```

## 5. Expected Runtimes

| Step | Approximate runtime on 4-core x86_64 |
|------|---------------------------------------|
| Docker image build (first time) | 4–6 min |
| Contract migration | 20 s |
| Gas measurement script | 15 s |
| Latency benchmark (N = 1000) | 2–3 min |
| Throughput sweep (5 configs × 200) | 4–6 min |
| Fraud detector evaluation (N = 5000) | 30 s |
| Hardhat test suite | 60 s |
| Python test suite | 20 s |

## 6. Determinism Notes

* **Ganache accounts.** `--deterministic` produces the same 10 accounts
  and private keys on every run. The seed values are:
  * `accounts[0]` — admin / deployer
  * `accounts[1]` — testing station
  * `accounts[2]` — OBD device
  * `accounts[3]` — vehicle owner
  These are the only keys used in benchmarks; the mapping is consistent
  across machines.
* **Random nonces.** The benchmark scripts generate nonces with
  `secrets.token_hex(32)` which uses OS entropy. This does not affect
  reproducibility of *aggregate* statistics.
* **Isolation Forest seed.** `random_state=42` is hard-coded in
  `ml/fraud_detector.py`. Re-training on the same input produces the
  same model.
* **Gas numbers.** The EVM gas schedule is fixed by the Shanghai fork;
  numbers reproduce to within 0.1 %.
* **Latency numbers.** Wall-clock figures will vary by hardware. Paper
  tables should therefore report relative ratios (e.g. fraud detector is
  X% of total) in addition to absolute numbers.

## 7. Artifact Bundle

A reproducibility bundle containing:

* Source at the tagged commit
* Compiled contract artifacts (`build/contracts/*.json`)
* Generated `docs/*.json` reports
* The Docker images used (`docker save` exports)

is produced by:

```bash
bash scripts/make_artifact.sh v3.1.0
# -> artifacts/smartpuc-v3.1.0.tar.gz
```

Upload this bundle to Zenodo or figshare and cite the DOI in the paper's
Data Availability statement.

## 8. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `Could not connect to localhost:7545` | Ganache not running | Start Ganache or `docker-compose up ganache` |
| `Error: signature mismatch` in `storeEmission` | `OBD_DEVICE_PRIVATE_KEY` does not match the address registered via `setRegisteredDevice` | Redeploy contracts or update env |
| `429 Rate limit exceeded` in benchmarks | Backend rate limiter is active | Pass `RATE_LIMIT_MAX=100000` in `.env` for benchmarking |
| `ImportError: tensorflow` | Optional dep not installed | LSTM predictor falls back to linear extrapolation; harmless |
| `ImportError: obd` | Optional real OBD dep not installed | System uses the WLTC simulator instead; harmless |
| Gas numbers differ by > 5 % | Different Solidity compiler | Pin `solc 0.8.21` in your environment |

## 9. Citing

If you build on this artifact, please cite the paper and include the artifact
DOI. A `CITATION.cff` file is provided at the repository root.
