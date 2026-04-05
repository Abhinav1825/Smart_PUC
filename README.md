# Smart PUC v3.2 — Blockchain-Based Vehicle Emission Monitoring System

![CI](https://github.com/your-org/Smart_PUC/actions/workflows/ci.yml/badge.svg)
![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)
![Solidity](https://img.shields.io/badge/Solidity-0.8.21-363636.svg)
![Node](https://img.shields.io/badge/Node.js-18%2B-339933.svg)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB.svg)
![Version](https://img.shields.io/badge/Version-v3.2-blueviolet.svg)

**Version:** v4.0 ("Major makeover 4.0", commit `0d54c32`)
**Target venue:** IEEE Transactions on Intelligent Transportation Systems (primary) / IEEE Internet of Things Journal (secondary)
**Status:** Research prototype — publication-ready pending Polygon Amoy testnet deployment

A **research prototype** of a blockchain-based real-time vehicle emission
monitoring and compliance system for India. It implements a **3-node trust
architecture** in which no single party can tamper with emission data,
tracks all five Bharat Stage VI pollutants (CO2, CO, NOx, HC, PM2.5) using
physics-based models and ML-assisted fraud detection, issues NFT-based
digital PUC certificates, and rewards compliant vehicles with ERC-20 Green
Credit Tokens redeemable through an on-chain marketplace.

> ⚠️ **Research prototype — not for live deployment.** This repository is
> designed to accompany an academic paper and to support reproducible
> experiments. Before any pilot rollout, read [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md),
> [docs/PRIVACY_DPDP.md](docs/PRIVACY_DPDP.md), and the
> **Limitations and Non-Goals** section below.

### Paper target venue

This artefact accompanies a manuscript being prepared for submission to
**IEEE Internet of Things Journal** (primary) / **IEEE Transactions on
Intelligent Transportation Systems** (secondary). Both venues accept
software artefacts and emphasise reproducibility, and both fit the paper's
blockchain + IoT + ITS framing. See [docs/PAPER_FRAMING.md](docs/PAPER_FRAMING.md)
for the intended narrative, the §IV.C CES-vs-CO2 experimental framing,
and the mapping between paper claims and this artefact's evidence files.

---

## 3-Node Trust Architecture

```
  Node 1: OBD Device          Node 2: Testing Station         Node 3: Verification Portal
  (Signs telemetry data)      (Validates + submits to chain)  (Read-only, no backend needed)
        |                              |                              |
        | ECDSA signature              | JWT auth + fraud detection   | Direct chain read
        v                              v                              v
  +------------------------------------------------------------------------+
  |                        Ethereum Blockchain                             |
  |  +------------------+  +-------------------+  +-------------------+    |
  |  | EmissionRegistry |  | PUCCertificate    |  | GreenToken        |    |
  |  | On-chain CES     |->| ERC-721 NFT certs |->| ERC-20 rewards    |    |
  |  | Nonce replay     |  | IPFS tokenURI     |  | Marketplace with  |    |
  |  | protection       |  | Base URI support  |  | 4 reward types    |    |
  |  +------------------+  +-------------------+  +-------------------+    |
  +------------------------------------------------------------------------+
```

**Why 3 nodes?** No single party can forge emission data:
- The **OBD device** signs data with its private key (proves data provenance)
- The **testing station** validates and runs fraud detection (independent verification)
- The **blockchain** stores records immutably with on-chain CES calculation (tamper-proof history)
- The **verification portal** reads directly from chain (no backend trust needed)

---

## Key Features

| Category | Feature | Status |
|----------|---------|--------|
| Smart Contracts | On-chain CES calculation (trustless scoring) | ✓ |
| Smart Contracts | Nonce-based replay protection | ✓ |
| Smart Contracts | bytes32 gas-optimized vehicle tracking | ✓ |
| Smart Contracts | Optional soft cap on vehicle count (pilot-mode only; disabled by default) | ✓ |
| Smart Contracts | O(1) violation index tracking | ✓ |
| Smart Contracts | IPFS tokenURI + base URI metadata linking | ✓ |
| Smart Contracts | GreenToken marketplace with 4 reward types | ✓ |
| Smart Contracts | Burn-to-redeem mechanism | ✓ |
| Backend | JWT authentication for authority endpoints | ✓ |
| Backend | Real OBD-II hardware support (ELM327 via python-obd) | ✓ |
| Backend | Analytics endpoints (trends, fleet, distribution, phases) | ✓ |
| Backend | Fleet management endpoints | ✓ |
| Backend | RTO integration endpoints | ✓ |
| Backend | Notification system | ✓ |
| Backend | Green Token marketplace endpoints | ✓ |
| Frontend | Analytics dashboard with Chart.js | ✓ |
| Frontend | Fleet management panel | ✓ |
| Frontend | RTO portal with compliance heatmap | ✓ |
| Frontend | Marketplace for token redemption | ✓ |
| Frontend | QR code generation + auto-verify from URL | ✓ |
| Testing | 33+ Hardhat + ethers v6 tests (incl. UUPS proxy semantics) | ✓ |
| CI/CD | 5-job GitHub Actions pipeline | ✓ |
| Deployment | Multi-chain: Ganache, Sepolia, Polygon, Amoy | ✓ |
| Deployment | Docker 3-node orchestration with healthchecks | ✓ |
| Core | WLTC Class 3b driving cycle simulation | -- |
| Core | EPA MOVES3 VSP operating mode model | -- |
| Core | 3-component ML fraud detection (Isolation Forest) | -- |
| Core | Emission forecasting (25s horizon; linear fallback by default, optional LSTM) | -- |
| Core | ERC-721 NFT PUC certificates | -- |
| Core | ERC-20 Green Credit Token rewards | -- |
| Core | VAHAN 4.0 vehicle registration bridge (simulated integration point) | -- |

---

## Smart Contract Architecture

| Contract | Standard | Key Capabilities |
|----------|----------|-----------------|
| **EmissionRegistry** | Custom | On-chain CES calculation, ECDSA device signature verification, nonce replay protection, bytes32 gas optimization, bounded vehicle tracking, O(1) violation index, role-based access (Admin/Station/Device), paginated reads |
| **PUCCertificate** | ERC-721 | NFT certificates with 180-day validity, auto-issuance after 3 consecutive passes, IPFS tokenURI support, base URI for metadata, revocable by authority, auto-mints GreenTokens on issuance |
| **GreenToken** | ERC-20 | 100 GCT per certificate, burn-to-redeem marketplace, 4 reward types (Toll Discount 50 GCT, Parking Waiver 30 GCT, Tax Credit 100 GCT, Priority Service 20 GCT), authorized minter pattern, on-chain redemption tracking |

### Pollutant Values and Scaling

| Data Type | Scaling | Example |
|-----------|---------|---------|
| Pollutants (CO2, CO, NOx, HC, PM2.5) | x1000 | 120.5 g/km stored as 120500 |
| CES / Fraud scores | x10000 | 0.85 stored as 8500 |
| VSP value | x1000 | 15.2 kW/ton stored as 15200 |

---

## Account Roles

| Account | Role | Permissions |
|---------|------|-------------|
| `accounts[0]` | Admin | Deploy contracts, manage system, register stations/devices, set reward costs |
| `accounts[1]` | Testing Station | Submit emission records, run fraud detection, trigger certificate issuance |
| `accounts[2]` | OBD Device | Sign telemetry data with ECDSA private key |
| `accounts[3]` | Vehicle Owner | Claim NFT certificates, receive GCT tokens, redeem rewards, transfer tokens |

---

## Security Features

| Layer | Mechanism | Description |
|-------|-----------|-------------|
| Blockchain | On-chain CES | CES calculated by the contract itself — stations cannot supply a falsified score |
| Blockchain | Nonce replay protection | Each submission includes a nonce; prevents replaying old telemetry |
| Blockchain | ECDSA signature verification | OBD device signatures verified on-chain to prove data provenance |
| Blockchain | ReentrancyGuard | OpenZeppelin protection on all state-changing functions |
| Blockchain | Role-based access | Admin, Testing Station, OBD Device roles enforced at contract level |
| Backend | JWT authentication | Token-based auth for authority and protected endpoints |
| Backend | Rate limiting | Per-IP request throttling to prevent abuse |
| Backend | API key auth | HMAC-based authentication for write endpoints |
| Frontend | XSS prevention | HTML escaping on all user-facing output |
| Data | Input validation | Bounds checking on all telemetry values before submission |
| Data | Fraud detection | 3-component ML ensemble (physics constraints, Isolation Forest, temporal consistency) |

---

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| **Node.js** | v18+ | [nodejs.org](https://nodejs.org) |
| **Python** | 3.10+ | [python.org](https://python.org) |
| **MetaMask** | Latest | [Chrome extension](https://metamask.io) |
| **Docker** (optional) | Latest | [docker.com](https://docker.com) |
| **ELM327 OBD-II adapter** (optional) | USB/Bluetooth | For real vehicle hardware integration |

---

## Quick Start

### Option A: Windows One-Click

```bash
run_project.bat
```

This starts Ganache, deploys contracts, launches the backend, OBD simulator, and frontend.

### Option B: Docker (Recommended)

```bash
docker-compose up --build
```

Starts 5 services with full healthchecks:

| Service | Port | Description |
|---------|------|-------------|
| `ganache` | 7545 | Local Ethereum blockchain |
| `deploy-contracts` | -- | One-shot: deploys all 3 contracts |
| `station` | 5000 | Testing Station FastAPI (uvicorn) |
| `obd-device` | -- | OBD Device simulator |
| `frontend` | 3000 | Static file server for all 7 pages |

### Option C: Manual Setup

```bash
# 1. Install dependencies
npm install
pip install -r requirements.txt

# 2. Start Ganache (separate terminal)
npx ganache --deterministic --accounts 10 --defaultBalanceEther 100 --port 7545

# 3. Deploy all 3 contracts
npx hardhat run scripts/deploy.js --network localhost

# 4. Start Testing Station backend (separate terminal, FastAPI + uvicorn)
python -m uvicorn backend.main:app --host 0.0.0.0 --port 5000 --reload

# 5. Start frontend (separate terminal)
npx http-server frontend -p 3000 -c-1 --cors

# 6. (Optional) Start OBD Device simulator
python -m obd_node.obd_device --count 50 --interval 3
```

---

## Frontend Pages

| # | Page | URL | Description |
|---|------|-----|-------------|
| 1 | **Vehicle Dashboard** | `localhost:3000/index.html` | Live emission metrics, route simulation, LSTM forecasting, NFT claiming, GCT balance display |
| 2 | **Authority Panel** | `localhost:3000/authority.html` | Issue/revoke certificates, manage OBD devices and testing stations, live violation alerts |
| 3 | **Verify PUC** | `localhost:3000/verify.html` | Public verification portal, QR code generation, auto-verify from URL parameters, print-friendly view |
| 4 | **Analytics** | `localhost:3000/analytics.html` | Chart.js trend visualizations, CES histogram, phase breakdown charts, CSV data export |
| 5 | **Fleet Management** | `localhost:3000/fleet.html` | Fleet vehicle table, compliance alerts, bulk certificate operations, comparison charts |
| 6 | **RTO Portal** | `localhost:3000/rto.html` | Compliance checking, flagged vehicle list, enforcement heatmap, regulatory reporting |
| 7 | **Marketplace** | `localhost:3000/marketplace.html` | Token balance display, reward catalog (4 types), redemption interface, token transfer |

---

## API Reference

### Authentication

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `POST` | `/api/auth/login` | None | Login with credentials, returns JWT token |

### Core Pipeline

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `POST` | `/api/record` | API Key | Full pipeline: validate, fraud detect, store on chain (accepts signed OBD data) |
| `GET` | `/api/simulate` | None | Generate WLTC telemetry + all 5 pollutants |
| `GET` | `/api/status` | None | System health + all contract addresses |

### Vehicle Data

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/api/history/<vehicleId>` | None | Paginated on-chain emission records |
| `GET` | `/api/violations` | None | All FAIL records across vehicles |
| `GET` | `/api/vehicle-stats/<vehicleId>` | None | Aggregated stats + certificate eligibility |
| `GET` | `/api/vehicle/verify/<registration>` | None | VAHAN registration check |
| `GET` | `/api/verify/<vehicleId>` | None | Public PUC verification (no auth) |

### Certificate Management

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `POST` | `/api/certificate/issue` | JWT | Issue PUC certificate NFT |
| `POST` | `/api/certificate/revoke` | JWT | Revoke an existing certificate |
| `GET` | `/api/certificate/<vehicleId>` | None | Certificate status from chain |

### Green Token & Marketplace

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/api/green-tokens/<address>` | None | Green Token balance for an address |
| `POST` | `/api/tokens/redeem` | JWT | Burn tokens to redeem a reward |
| `GET` | `/api/tokens/rewards` | None | List available reward types and costs |
| `GET` | `/api/tokens/history/<address>` | None | Redemption history for an address |

### Analytics

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/api/analytics/trends/<vehicleId>` | None | CES trend data over time |
| `GET` | `/api/analytics/fleet` | JWT | Fleet-wide emission statistics |
| `GET` | `/api/analytics/distribution` | None | CES score distribution histogram |
| `GET` | `/api/analytics/phase-breakdown/<vehicleId>` | None | Emissions by WLTC driving phase |

### Fleet Management

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/api/fleet/vehicles` | JWT | List all fleet vehicles with status |
| `GET` | `/api/fleet/alerts` | JWT | Active fleet compliance alerts |

### RTO Integration

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/api/rto/check/<vehicleId>` | JWT | RTO compliance check for a vehicle |
| `GET` | `/api/rto/flagged` | JWT | List of flagged/non-compliant vehicles |

### Notifications & OBD Hardware

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/api/notifications` | JWT | System notifications (cert expiry, violations, alerts) |
| `GET` | `/api/obd/status` | None | OBD-II hardware connection status |
| `POST` | `/api/obd/read` | API Key | Read live data from connected OBD-II device |

---

## Green Token Marketplace

Compliant vehicles earn **100 GCT** per PUC certificate. Tokens are redeemable through the on-chain burn-to-redeem marketplace:

| Reward Type | Cost (GCT) | ID | Description |
|-------------|------------|-----|-------------|
| Toll Discount | 50 | `TOLL_DISCOUNT (0)` | 50% discount on highway toll charges |
| Parking Waiver | 30 | `PARKING_WAIVER (1)` | Free municipal parking for 30 days |
| Tax Credit | 100 | `TAX_CREDIT (2)` | Road tax credit applied to next renewal |
| Priority Service | 20 | `PRIORITY_SERVICE (3)` | Priority lane at RTO service centers |

**Redemption flow:**
1. User selects reward type on the marketplace page
2. Frontend calls `/api/tokens/redeem` with reward type
3. Backend calls `GreenToken.redeemReward()` which burns the required tokens
4. On-chain `Redemption` record is created with a unique ID
5. User receives a confirmation with the redemption ID

---

## Testing

### Solidity Tests (33+ Hardhat tests across 3 contracts)

```bash
npx hardhat test            # run the full suite
npm run test:gas            # run with gas reporter enabled
```

Covers:
- **EmissionRegistry:** record submission, on-chain CES calculation, nonce replay protection, role access, device signature verification, soft vehicle cap, violation indexing.
- **PUCCertificate:** issuance, revocation, eligibility checks, IPFS tokenURI, metadata linking, proportional Green Token reward.
- **GreenToken:** minting, balance tracking, marketplace redemption, burn mechanics, reward cost validation.
- **UUPS proxy semantics:** state preservation across upgrades, unauthorized upgrade rejection.

Edge cases tested: replay attacks, invalid signatures, overflow protection, gas optimization, unauthorized access, upgrade authorization.

### Python Tests

```bash
# Run all tests with coverage
python -m pytest tests/ -v --cov=backend --cov=ml --cov=physics --cov=integrations --cov-report=term-missing

# Individual test modules
python -m pytest tests/test_emission_engine.py -v
python -m pytest tests/test_fraud_detector.py -v
python -m pytest tests/test_integration.py -v
```

Test modules: `test_blockchain_connector`, `test_emission_engine`, `test_fraud_detector`, `test_integration`, `test_lstm_predictor`, `test_obd_adapter`, `test_simulator`, `test_vaahan_bridge`, `test_vsp_model`.

---

## CI/CD Pipeline

The GitHub Actions workflow (`.github/workflows/ci.yml`) runs **5 parallel jobs** on every push and pull request to `main`:

| Job | Runner | What It Does |
|-----|--------|-------------|
| **solidity-tests** | ubuntu-latest | Compile contracts (Hardhat), run the full test suite on the in-process Hardhat network, emit gas report |
| **solidity-security** | ubuntu-latest | Run Slither static analysis for vulnerability detection |
| **python-tests** | ubuntu-latest | Run pytest with coverage across backend, ml, physics, integrations; upload coverage artifact |
| **lint** | ubuntu-latest | Flake8 linting on all Python modules (max-line-length=120, max-complexity=15) |
| **docker-build** | ubuntu-latest | Build all 3 Dockerfiles to verify they compile successfully |

---

## Multi-Chain Deployment

| Network | Chain ID | RPC | Use Case |
|---------|----------|-----|----------|
| **Ganache** | 5777 | `localhost:7545` | Local development and testing |
| **Sepolia** | 11155111 | Infura | Ethereum testnet deployment |
| **Polygon Mainnet** | 137 | Infura | Production deployment (low gas fees) |
| **Polygon Amoy** | 80002 | Infura | Polygon testnet for staging |

### Deploying to Testnets/Mainnet

```bash
# Create .env file with credentials
echo "MNEMONIC=your twelve word mnemonic phrase here" > .env
echo "INFURA_PROJECT_ID=your_infura_project_id" >> .env

# Deploy to Sepolia
npx hardhat run scripts/deploy.js --network sepolia

# Deploy to Polygon Amoy testnet
npx hardhat run scripts/deploy.js --network amoy

# Deploy to Polygon mainnet
npx hardhat run scripts/deploy.js --network polygon

# Polygon zkEVM / Arbitrum
npx hardhat run scripts/deploy.js --network zkevm
npx hardhat run scripts/deploy.js --network arbitrum
```

---

## Real OBD-II Hardware Integration

Smart PUC supports direct connection to real vehicle OBD-II ports via ELM327 adapters.

### Supported Hardware

- ELM327 USB adapters
- ELM327 Bluetooth adapters
- Any python-obd compatible interface

### Connection Setup

```bash
# 1. Install the optional OBD dependency
pip install obd==0.7.2

# 2. Connect ELM327 adapter to vehicle OBD-II port

# 3. Check connection status
curl http://localhost:5000/api/obd/status

# 4. Read live data from the vehicle
curl -X POST http://localhost:5000/api/obd/read \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_api_key"
```

### Supported OBD-II PIDs

| PID | Parameter | Unit |
|-----|-----------|------|
| `0x0C` | Engine RPM | rev/min |
| `0x0D` | Vehicle Speed | km/h |
| `0x10` | MAF Air Flow Rate | g/s |
| `0x11` | Throttle Position | % |
| `0x05` | Engine Coolant Temperature | C |

The OBD adapter module (`integrations/obd_adapter.py`) decodes raw PID data per SAE J1979 and feeds it into the emission engine for real-time pollutant calculation.

---

## Project Structure

```
Smart_PUC/
├── contracts/
│   ├── EmissionRegistry.sol     # On-chain CES, nonce replay, bytes32 optimization
│   ├── PUCCertificate.sol       # ERC-721 NFT certs, IPFS tokenURI, base URI
│   └── GreenToken.sol           # ERC-20 rewards, burn-to-redeem marketplace
├── scripts/
│   ├── deploy.js                # Hardhat deployment script (UUPS proxies)
│   ├── flatten_artifacts.js     # Flattens Hardhat artifacts into Truffle-compat build/contracts/
│   ├── measure_gas.js           # Per-operation gas measurement harness
│   ├── bench_latency.py         # End-to-end latency benchmark
│   ├── bench_throughput.py      # Concurrent throughput sweep
│   └── compute_sri.py           # SRI hash injector for frontend CDN assets
├── test/
│   └── SmartPUC.test.js         # 33+ Hardhat/ethers tests across all 3 contracts + UUPS semantics
├── obd_node/
│   └── obd_device.py            # Node 1: OBD device simulator + ECDSA signing
├── backend/
│   ├── main.py                  # Node 2: FastAPI app (27+ endpoints, JWT, analytics)
│   ├── schemas.py               # Pydantic request/response models
│   ├── dependencies.py          # Auth + rate-limit FastAPI dependencies
│   ├── persistence.py           # SQLite store (rate limit, notifications, Merkle batches)
│   ├── merkle_batch.py          # Keccak256 Merkle tree + batcher for hot/cold path
│   ├── blockchain_connector.py  # Multi-contract Web3.py connector
│   ├── emission_engine.py       # Multi-pollutant BSVI emission calculator
│   └── simulator.py             # WLTC Class 3b driving cycle generator
├── frontend/
│   ├── index.html               # Vehicle Owner Dashboard
│   ├── authority.html           # Authority Panel (certs, devices, stations)
│   ├── verify.html              # Public Verification Portal + QR codes
│   ├── analytics.html           # Analytics Dashboard (Chart.js)
│   ├── fleet.html               # Fleet Management Panel
│   ├── rto.html                 # RTO Portal (compliance, flagging)
│   ├── marketplace.html         # Green Token Marketplace
│   ├── app.js                   # Multi-contract frontend logic
│   └── style.css                # Dark theme stylesheet
├── physics/
│   └── vsp_model.py             # EPA MOVES3 Vehicle Specific Power model
├── ml/
│   ├── fraud_detector.py        # 3-component ensemble fraud detection
│   ├── lstm_predictor.py        # Emission forecasting (25s horizon)
│   └── generate_training_data.py
├── integrations/
│   ├── obd_adapter.py           # OBD-II PID decoder (SAE J1979)
│   └── vaahan_bridge.py         # VAHAN 4.0 vehicle registration bridge
├── tests/                       # Python test suite (pytest + coverage)
│   ├── test_blockchain_connector.py
│   ├── test_emission_engine.py
│   ├── test_fraud_detector.py
│   ├── test_integration.py
│   ├── test_lstm_predictor.py
│   ├── test_obd_adapter.py
│   ├── test_simulator.py
│   ├── test_vaahan_bridge.py
│   └── test_vsp_model.py
├── benchmarks/                  # Scalability + gas cost experiments
├── .github/workflows/ci.yml     # 5-job CI/CD pipeline
├── docker-compose.yml           # 3-node Docker orchestration
├── Dockerfile.backend           # Testing Station container
├── Dockerfile.obd               # OBD Device container
├── Dockerfile.deploy            # Contract deployment container
├── hardhat.config.js            # Hardhat + 8 network entries (Ganache, Sepolia, Polygon, Amoy, zkEVM, Arbitrum, etc.)
├── package.json                 # Node.js dependencies (v3.1.0)
├── requirements.txt             # Python dependencies
└── run_project.bat              # Windows one-click setup
```

---

## Tech Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Blockchain** | Solidity 0.8.21 | Smart contract language |
| **Blockchain** | OpenZeppelin 4.9.6 | ERC-721, ERC-20, ReentrancyGuard, ECDSA |
| **Blockchain** | Hardhat + ethers v6 | Compilation, deployment, testing framework |
| **Blockchain** | OpenZeppelin Upgrades (UUPS) | Proxy-based contract upgradeability |
| **Blockchain** | Ganache | Local Ethereum development blockchain |
| **Backend** | Python 3.10+ | Testing Station server runtime |
| **Backend** | FastAPI 0.115 + uvicorn | REST API framework (async, auto-generated OpenAPI at /docs) |
| **Backend** | Pydantic v2 | Request / response validation |
| **Backend** | Web3.py 6.15 | Ethereum blockchain interaction |
| **Backend** | PyJWT 2.8 | JWT token authentication |
| **Backend** | scikit-learn 1.4 | Isolation Forest fraud detection |
| **Backend** | NumPy 1.26 | Numerical computation for emission models |
| **Frontend** | HTML5 / CSS3 / JS | 7-page dashboard application |
| **Frontend** | Web3.js | Browser-side blockchain interaction via MetaMask |
| **Frontend** | Chart.js | Analytics visualizations and trend charts |
| **Hardware** | python-obd | ELM327 OBD-II adapter communication |
| **DevOps** | Docker Compose | Multi-container orchestration |
| **DevOps** | GitHub Actions | 5-job CI/CD pipeline |
| **Networks** | Infura | Sepolia and Polygon RPC provider |
| **Networks** | HDWalletProvider | Mnemonic-based testnet/mainnet deployment |

---

## BSVI Compliance Thresholds

| Pollutant | Threshold (g/km) | CES Weight |
|-----------|-------------------|------------|
| CO2 | 120 | 35% |
| NOx | 0.06 | 30% |
| CO | 1.0 | 15% |
| HC | 0.10 | 12% |
| PM2.5 | 0.0045 | 8% |

**Composite Emission Score:** `CES = sum(pollutant_i / threshold_i * weight_i)`. Vehicle passes if `CES < 1.0`.

---

## Limitations and Non-Goals

This repository is a research prototype. Paper reviewers, recruiters, and
pilot operators should understand the boundaries of what it claims.

- **Private key protection.** Device and station private keys are loaded
  from `.env` for reproducibility. Any real deployment must move the OBD
  device key into a secure element (ATECC608A, TPM 2.0, ARM TrustZone).
  See [docs/THREAT_MODEL.md §5.7](docs/THREAT_MODEL.md).
- **VAHAN integration is simulated.** `integrations/vaahan_bridge.py` ships
  a `MockVaahanService` with ~10 hand-coded vehicles. The real Parivahan
  VAHAN 4.0 API is access-controlled; the production hook is clearly
  marked in the source.
- **Privacy.** Vehicle registrations are stored as plaintext on-chain in
  the default configuration. This is acceptable for an academic prototype
  but incompatible with India's DPDP Act 2023 and the EU GDPR. The
  mitigation plan (salted hash, commitment, or zkPUC) is documented in
  [docs/PRIVACY_DPDP.md](docs/PRIVACY_DPDP.md).
- **LSTM forecasting.** The predictor module ships a linear-extrapolation
  fallback by default (`use_lstm=False`). The TensorFlow-based LSTM path
  exists for completeness but is not part of the headline results; retrain
  it yourself if you want the deep-learning numbers.
- **In-memory rate limiting and notifications** have been replaced by
  SQLite persistence in v3.1. Horizontal scaling beyond a single process
  still requires Redis or Postgres — see [docs/ARCHITECTURE_TRADEOFFS.md](docs/ARCHITECTURE_TRADEOFFS.md).
- **Single-chain deployment.** Polygon PoS is the default. zkEVM and
  Arbitrum network entries are in `hardhat.config.js` but have not been
  end-to-end tested on those chains.
- **No admin multisig yet.** The `admin` role in every contract is a single
  EOA in the current deployment. Production must use a 2-of-3 (or better)
  multisig.

## Paper and Reproducibility

Documentation intended to accompany an academic paper lives in [docs/](docs/):

| Document | Purpose |
|----------|---------|
| [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) | Formal adversary model, mitigation table, hardening checklist |
| [docs/PRIVACY_DPDP.md](docs/PRIVACY_DPDP.md) | DPDP Act / GDPR gap analysis, pseudonymisation plan, zkPUC sketch |
| [docs/GAS_ANALYSIS.md](docs/GAS_ANALYSIS.md) | Per-operation gas cost table, fiat projections, national-scale cost model |
| [docs/BENCHMARKS.md](docs/BENCHMARKS.md) | End-to-end latency and throughput methodology + results |
| [docs/ARCHITECTURE_TRADEOFFS.md](docs/ARCHITECTURE_TRADEOFFS.md) | Design decisions and rejected alternatives |
| [docs/FRAUD_EVALUATION.md](docs/FRAUD_EVALUATION.md) | Labelled-attack dataset, precision/recall/F1 of the fraud detector |
| [docs/REPRODUCIBILITY.md](docs/REPRODUCIBILITY.md) | Deterministic reproduction steps for every published number |

Each of those documents has a **§ Reproducing These Numbers** section that
lists a single command to regenerate the JSON reports the paper cites from.
All scripts live under [scripts/](scripts/) and [benchmarks/](benchmarks/).

## License

MIT
