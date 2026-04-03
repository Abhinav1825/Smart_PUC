# Smart PUC — Multi-Pollutant Vehicle Emission Monitoring System

Blockchain-Based Real-Time Multi-Pollutant Vehicle Emission Monitoring and Compliance System.

This project implements a transparent, tamper-proof alternative to traditional Pollution Under Control (PUC) certificates. It monitors **all 5 Bharat Stage VI regulated pollutants** (CO2, CO, NOx, HC, PM2.5) using physics-based models, ML fraud detection, and NFT-based digital certificates.

## Architecture

```
+-------------------+     +------------------+     +-------------------+
|   WLTC Simulator  | --> |  VSP Physics     | --> | Multi-Pollutant   |
|   (1800s cycle)   |     |  Model (EPA)     |     | Emission Engine   |
+-------------------+     +------------------+     +-------------------+
                                                           |
                                                           v
+-------------------+     +------------------+     +-------------------+
|   NFT PUC         | <-- |  Blockchain      | <-- | Fraud Detector    |
|   Certificate     |     |  (Solidity)      |     | (Ensemble ML)     |
+-------------------+     +------------------+     +-------------------+
                                                           |
                                                           v
                                                   +-------------------+
                                                   | LSTM Predictor    |
                                                   | (Preventive)      |
                                                   +-------------------+
                                                           |
                                                           v
                                                   +-------------------+
                                                   | Dashboard         |
                                                   | (5 pollutants +   |
                                                   |  fraud + LSTM)    |
                                                   +-------------------+
```

### Layers

1. **Smart Contracts (Solidity)**: `EmissionContract.sol` stores all 5 pollutants + CES + fraud score per record. `PUCCertificate.sol` issues ERC-721 NFT certificates with 180-day expiry.
2. **Physics Engine (Python)**: VSP model (EPA MOVES3), WLTC Class 3b driving cycle, multi-pollutant emission rates with Arrhenius NOx correction, cold-start penalties (COPERT 5), and Composite Emission Score.
3. **ML Layer (Python)**: Three-component ensemble fraud detector (physics constraints + Isolation Forest + temporal consistency). LSTM predictor for preventive compliance warnings.
4. **Backend API (Flask)**: Orchestrates the full pipeline: Simulator -> VSP -> Emissions -> Fraud -> Blockchain -> Response.
5. **Frontend Dashboard (HTML/JS)**: Real-time display of all 5 pollutants, CES gauge, fraud alerts, LSTM prediction chart, WLTC phase indicator, NFT certificate viewer.

## Key Features

- **5 BSVI Pollutants**: CO2 (120 g/km), CO (1.0 g/km), NOx (0.06 g/km), HC (0.10 g/km), PM2.5 (0.0045 g/km)
- **Composite Emission Score (CES)**: Weighted multi-pollutant metric (CO2=35%, NOx=30%, CO=15%, HC=12%, PM2.5=8%). CES < 1.0 = PASS
- **WLTC Driving Cycle**: Full 1800-second Class 3b profile with 4 phases (Low/Medium/High/Extra High)
- **VSP Physics Model**: EPA MOVES Vehicle Specific Power formula with operating mode bins
- **Fraud Detection**: Ensemble of physics validator, Isolation Forest, and temporal consistency checker
- **LSTM Prediction**: Forecasts emissions 25 seconds ahead; warns before violations occur
- **NFT PUC Certificates**: ERC-721 digital certificates with 180-day validity and revocation
- **VAHAN Integration**: Bridge to India's vehicle registration database (with mock fallback)

## Prerequisites

- **Node.js** (v18+) and **npm**
- **Python** (3.10+) with pip
- **Ganache** (UI or CLI) for local blockchain
- **MetaMask** browser extension
- **Truffle** (`npm install -g truffle`)

## Quick Start

### Option 1: One-Click Setup (Windows)
```bash
run_project.bat
```

### Option 2: Manual Setup

#### 1. Blockchain Setup
```bash
# Start Ganache on port 7545
npx ganache -d -p 7545

# Install dependencies and deploy contracts
npm install
npm run compile
npm run migrate
```

#### 2. Backend Setup
```bash
cp .env.example .env
# Edit .env: set PRIVATE_KEY from Ganache Account 0

cd backend
python -m venv venv
# Windows: venv\Scripts\activate
# Mac/Linux: source venv/bin/activate
pip install -r ../requirements.txt
python app.py
```

#### 3. Frontend
```bash
npm run dev:frontend
# Open http://127.0.0.1:8080
# Connect MetaMask (Ganache network, Chain ID 1337)
```

## Project Structure

```
Smart_PUC/
|-- backend/
|   |-- app.py                    # Flask API (main pipeline orchestrator)
|   |-- emission_engine.py        # Multi-pollutant BSVI emission calculator
|   |-- simulator.py              # WLTC Class 3b driving cycle simulator
|   |-- blockchain_connector.py   # Web3.py multi-pollutant blockchain interface
|   |-- emission_engine_legacy.py # Backup of original CO2-only engine
|   |-- simulator_legacy.py       # Backup of original random simulator
|-- physics/
|   |-- vsp_model.py              # EPA MOVES VSP model + operating mode bins
|-- ml/
|   |-- fraud_detector.py         # Ensemble fraud detection (3 components)
|   |-- lstm_predictor.py         # LSTM emission forecasting
|-- contracts/
|   |-- EmissionContract.sol      # Multi-pollutant + CES + fraud storage
|   |-- PUCCertificate.sol        # ERC-721 NFT PUC certificate
|-- frontend/
|   |-- index.html                # Vehicle owner dashboard
|   |-- authority.html            # Authority/RTO dashboard
|   |-- app.js                    # Frontend logic (Chart.js + Ethers.js)
|   |-- style.css                 # Premium dark theme
|-- benchmarks/
|   |-- scalability_test.py       # 5-experiment benchmark suite
|   |-- blockchain_comparison.py  # Ethereum vs Polygon vs Hyperledger
|-- integrations/
|   |-- vaahan_bridge.py          # VAHAN 4.0 vehicle verification bridge
|-- tests/
|   |-- test_vsp_model.py
|   |-- test_emission_engine.py
|   |-- test_simulator.py
|   |-- test_fraud_detector.py
|   |-- test_lstm_predictor.py
|-- test/
|   |-- TestEmission.js           # Truffle smart contract tests
```

## Running Tests

### Python Tests
```bash
python -m pytest tests/ -v
```

### Solidity Tests
```bash
truffle test
```

### Benchmarks
```bash
python benchmarks/scalability_test.py
python benchmarks/blockchain_comparison.py
```

## BSVI Compliance Thresholds

| Pollutant | Threshold | CES Weight |
|-----------|-----------|------------|
| CO2       | 120 g/km  | 35%        |
| CO        | 1.0 g/km  | 15%        |
| NOx       | 0.06 g/km | 30%        |
| HC        | 0.10 g/km | 12%        |
| PM2.5     | 0.0045 g/km | 8%       |

## Academic References

- US EPA MOVES3 Technical Report (2020) — VSP model, operating mode bins
- ARAI BSVI Notification, MoRTH India (2020) — emission thresholds
- COPERT 5 Methodology, EEA Technical Report No. 19 — cold start corrections
- Ntziachristos & Samaras, EMEP/EEA (2019) — emission factors
- UN ECE Regulation No. 154 (WLTP), Annex 1 — WLTC cycle data
- Heywood, "Internal Combustion Engine Fundamentals" — Arrhenius NOx
- Rakha et al. (2004) — VSP to fuel rate polynomial
- Liu et al., "Isolation Forest", ICDM 2008 — anomaly detection
- Kwon et al., CAN Bus Anomaly Detection, IEEE TIFS 2021 — OBD security

## Testnet Deployment (Sepolia)

1. Set `INFURA_PROJECT_ID` and `MNEMONIC` in `.env`
2. Deploy: `npm run migrate:sepolia`
3. Update `RPC_URL` in `.env` and restart backend
4. Switch MetaMask to Sepolia network

## License

MIT
