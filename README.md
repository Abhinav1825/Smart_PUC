# Smart PUC — Multi-Pollutant Vehicle Emission Monitoring System

Blockchain-based real-time vehicle emission monitoring and compliance system for India. Tracks **all 5 Bharat Stage VI regulated pollutants** (CO2, CO, NOx, HC, PM2.5) using physics-based models, ML fraud detection, and NFT-based digital PUC certificates.

Built with: Solidity | Web3.py | Flask | WLTC | EPA MOVES VSP | Isolation Forest | LSTM | ERC-721 NFT

---

## Architecture

```
  WLTC Simulator ──> VSP Physics ──> Multi-Pollutant Engine ──> Fraud Detector
  (1800s cycle)      (EPA MOVES)     (5 BSVI pollutants)       (3-component ML)
                                            |                         |
                                            v                         v
  NFT PUC Cert <── Blockchain (Solidity) <──┘    LSTM Predictor ──> Dashboard
  (ERC-721)        (EmissionContract +           (25s forecast)     (Real-time
                    PUCCertificate)                                   5 pollutants)
```

**Pipeline flow:** OBD-II data --> WLTC Simulator --> VSP Model --> Emission Engine (CO2, CO, NOx, HC, PM2.5 + CES score) --> Fraud Detection (physics + Isolation Forest + temporal) --> Blockchain Storage --> Dashboard

---

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| **Node.js** | v18+ | [nodejs.org](https://nodejs.org) |
| **Python** | 3.10+ | [python.org](https://python.org) |
| **MetaMask** | Latest | [Chrome extension](https://metamask.io) |

Truffle, Ganache, and all other dependencies are installed automatically.

---

## First-Time Setup (Single Command)

Open a terminal in the project root folder and run:

**Windows (CMD or PowerShell):**
```bash
run_project.bat
```

This single script will:
1. Install all Node.js dependencies (Truffle, OpenZeppelin, http-server)
2. Install Truffle and Ganache globally
3. Create a Python virtual environment and install all Python packages
4. Generate the `.env` configuration file
5. Start Ganache (local blockchain) on port 7545
6. Compile and deploy both smart contracts (EmissionContract + PUCCertificate)
7. Start the Flask backend API on port 5000
8. Start the frontend dashboard on port 3000
9. Open `http://127.0.0.1:3000` in your browser

**Manual single-command alternative (Git Bash / WSL / Mac / Linux):**
```bash
npm install && npm install -g truffle ganache && \
python -m venv backend/venv && \
backend/venv/Scripts/pip install -r requirements.txt && \
echo "RPC_URL=http://127.0.0.1:7545" > .env && \
echo "PRIVATE_KEY=0x4f3edf983ac636a65a842ce7c78d9aa706d3b113bce9c46f30d7d21715b23b1d" >> .env && \
echo "FLASK_PORT=5000" >> .env && \
echo "FLASK_DEBUG=true" >> .env && \
echo "DEFAULT_VEHICLE_ID=MH12AB1234" >> .env && \
ganache -d -p 7545 &
sleep 5 && truffle migrate --reset && \
cd backend && ../backend/venv/Scripts/python app.py &
cd .. && npx http-server frontend -p 3000 -c-1 --cors
```

---

## Starting the Project (After First-Time Setup)

Everything is already installed. Just start the 3 services:

**Windows (One Command):**
```bash
run_project.bat
```

**Or manually in 3 terminals:**

| Terminal | Command | What it does |
|----------|---------|-------------|
| Terminal 1 | `ganache -d -p 7545` | Starts local blockchain |
| Terminal 2 | `truffle migrate --reset && cd backend && venv\Scripts\activate && python app.py` | Deploys contracts + starts API |
| Terminal 3 | `npx http-server frontend -p 3000 -c-1 --cors` | Starts dashboard |

Then open **http://127.0.0.1:3000** in your browser.

---

## How to Use

### 1. Open the Dashboard
Go to **http://127.0.0.1:3000** in your browser.

### 2. Connect MetaMask (Optional)
- Add a custom network in MetaMask:
  - **Network Name:** Ganache
  - **RPC URL:** `http://127.0.0.1:7545`
  - **Chain ID:** `1337`
  - **Currency:** ETH
- Import an account using this private key (Ganache Account 2):
  ```
  0x6cbed15c793ce57650b9877cf6fa156fbef513c4e6134f022a85b1ffdd59b2a1
  ```
- Click **Connect Wallet** on the dashboard

### 3. Start a Simulation
- Enter a vehicle registration number (default: `MH12AB1234`)
- Pick a **Mumbai route** from the dropdown (e.g., "Bandra - Andheri")
- Click **Start Route**

### 4. What Happens Every 3 Seconds
The car moves along the map and the system automatically:

1. **Generates telemetry** from the WLTC driving cycle (speed, RPM, fuel rate, acceleration)
2. **Calculates VSP** using the EPA MOVES Vehicle Specific Power formula
3. **Computes 5 pollutants** (CO2, CO, NOx, HC, PM2.5) with temperature and cold-start corrections
4. **Calculates CES** (Composite Emission Score) — the single compliance metric
5. **Runs fraud detection** (physics + Isolation Forest + temporal checks)
6. **Runs LSTM prediction** forecasting emissions 25 seconds ahead
7. **Writes to blockchain** (immutable record on Ganache)
8. **Updates the dashboard** with all live metrics

### 5. Dashboard Elements

| Element | What it shows |
|---------|--------------|
| **Map** | Car moving along a real Mumbai route (via OSRM) |
| **CES Gauge** | Semicircular gauge (green = PASS, red = FAIL) |
| **WLTC Phase** | L / M / H / EH indicator for current driving phase |
| **8 Metric Cards** | RPM, Speed, Fuel, CO2, CO, NOx, HC, PM2.5 with bars |
| **Compliance Badge** | Big PASS/FAIL with CES value |
| **Fraud Alert** | Red banner if tampering detected (score >= 0.65) |
| **LSTM Chart** | Predicted CES for next 25 seconds |
| **Latest Tx** | Blockchain transaction hash, block number |
| **NFT Certificate** | PUC certificate status, expiry date |
| **Vehicle Stats** | Total records, violations, fraud alerts, avg CES |
| **History Table** | All records with all 5 pollutant columns |

### 6. Authority Dashboard
Click **Authority Panel** in the navbar to see aggregated stats, filter by violations or fraud alerts, and watch real-time violation events.

### 7. API Endpoints
The backend exposes these REST endpoints:

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/status` | System health + module availability |
| `GET` | `/api/simulate` | Generate one telemetry reading |
| `POST` | `/api/record` | Full pipeline: calculate + detect + store on-chain |
| `GET` | `/api/history/<vehicleId>` | All on-chain records for a vehicle |
| `GET` | `/api/violations` | All FAIL records across vehicles |
| `GET` | `/api/vehicle-stats/<vehicleId>` | Aggregated stats |
| `GET` | `/api/certificate/<vehicleId>` | PUC certificate status |

**Example:**
```bash
curl -X POST http://127.0.0.1:5000/api/record \
  -H "Content-Type: application/json" \
  -d '{"vehicle_id":"MH12AB1234","speed":60,"rpm":2500,"fuel_rate":7.0}'
```

---

## Project Structure

```
Smart_PUC/
|-- backend/
|   |-- app.py                    # Flask API — pipeline orchestrator
|   |-- emission_engine.py        # Multi-pollutant BSVI emission calculator
|   |-- simulator.py              # WLTC Class 3b driving cycle simulator
|   |-- blockchain_connector.py   # Web3.py blockchain interface
|   |-- emission_engine_legacy.py # Original CO2-only engine (backup)
|   |-- simulator_legacy.py       # Original random simulator (backup)
|-- physics/
|   |-- vsp_model.py              # EPA MOVES VSP + operating mode bins
|-- ml/
|   |-- fraud_detector.py         # Ensemble fraud detection (3 components)
|   |-- lstm_predictor.py         # LSTM emission forecasting
|   |-- generate_training_data.py # LSTM training data generator
|-- contracts/
|   |-- EmissionContract.sol      # Multi-pollutant + CES + fraud + access control
|   |-- PUCCertificate.sol        # ERC-721 NFT PUC certificate
|-- frontend/
|   |-- index.html                # Vehicle owner dashboard
|   |-- authority.html            # Authority/RTO dashboard
|   |-- app.js                    # Frontend logic (Chart.js + Ethers.js)
|   |-- style.css                 # Dark theme stylesheet
|-- integrations/
|   |-- vaahan_bridge.py          # VAHAN 4.0 vehicle verification
|   |-- obd_adapter.py            # OBD-II PID mapping (SAE J1979)
|-- benchmarks/
|   |-- scalability_test.py       # 5-experiment benchmark suite
|   |-- blockchain_comparison.py  # Ethereum vs Polygon vs Hyperledger
|-- tests/                        # Python unit + integration tests
|-- test/                         # Solidity (Truffle) tests
|-- run_project.bat               # One-click Windows startup script
```

---

## Running Tests

```bash
# Python tests (65 tests — unit + integration)
python -m unittest discover tests -v

# Solidity tests (requires Ganache running)
truffle test

# Benchmarks
python benchmarks/scalability_test.py

# Generate LSTM training data
python -m ml.generate_training_data --cycles 3 --output ml/training_data.npy
```

---

## BSVI Compliance Thresholds

| Pollutant | Threshold (g/km) | CES Weight | What it is |
|-----------|----------------:|:----------:|------------|
| CO2 | 120.0 | 35% | Carbon dioxide (greenhouse gas) |
| NOx | 0.06 | 30% | Nitrogen oxides (smog, acid rain) |
| CO | 1.0 | 15% | Carbon monoxide (toxic) |
| HC | 0.10 | 12% | Hydrocarbons (ozone precursor) |
| PM2.5 | 0.0045 | 8% | Fine particulate matter (lung damage) |

**CES < 1.0 = PASS** | **CES >= 1.0 = FAIL**

---

## Key Technologies

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Smart Contracts | Solidity 0.8.21, OpenZeppelin 4.9 | On-chain emission storage + NFT certs |
| Backend | Python 3.10+, Flask, Web3.py | Pipeline orchestration + API |
| Physics | EPA MOVES3 VSP model | Vehicle power demand calculation |
| Driving Cycle | WLTC Class 3b (UN ECE R154) | Standardized test cycle simulation |
| Emissions | MOVES + IPCC + COPERT 5 | Multi-pollutant calculation |
| Fraud Detection | Isolation Forest + physics rules | OBD-II data tampering detection |
| Prediction | LSTM / linear extrapolation | Preventive compliance warnings |
| Frontend | HTML/CSS/JS, Chart.js, Leaflet, Ethers.js | Real-time dashboard |
| Blockchain | Ganache (local) / Sepolia (testnet) | Immutable record storage |
| Maps | Leaflet + OSRM | Route visualization |

---

## Testnet Deployment (Sepolia / Polygon)

For public deployment instead of local Ganache:

1. Get an [Infura](https://infura.io) project ID
2. Get Sepolia test ETH from a [faucet](https://sepoliafaucet.com)
3. Update `.env`:
   ```
   RPC_URL=https://sepolia.infura.io/v3/YOUR_PROJECT_ID
   MNEMONIC=your twelve word mnemonic phrase here
   ```
4. Deploy: `truffle migrate --network sepolia`
5. Update `.env` with the new `CONTRACT_ADDRESS`
6. Restart the backend

---

## Academic References

These are cited in the source code docstrings:

| Reference | Used in |
|-----------|---------|
| US EPA MOVES3 Technical Report (2020) | VSP model, operating mode bins |
| ARAI BSVI Notification, MoRTH India (2020) | Emission thresholds |
| COPERT 5 Methodology, EEA Technical Report No. 19 | Cold-start corrections |
| Ntziachristos & Samaras, EMEP/EEA (2019) | Emission factors |
| UN ECE Regulation No. 154 (WLTP), Annex 1 | WLTC driving cycle |
| Heywood, "Internal Combustion Engine Fundamentals" | Arrhenius NOx correction |
| Rakha et al. (2004) | VSP to fuel rate polynomial |
| Liu et al., "Isolation Forest", ICDM 2008 | Anomaly detection |
| Kwon et al., CAN Bus Anomaly Detection, IEEE TIFS 2021 | OBD-II security |
| Hochreiter & Schmidhuber (1997) | LSTM architecture |

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Port 8080 already in use | Use port 3000: `npx http-server frontend -p 3000 -c-1 --cors` |
| `truffle: command not found` | Run `npm install -g truffle` |
| `ganache: command not found` | Run `npm install -g ganache` |
| Backend says "Blockchain not connected" | Make sure Ganache is running on port 7545 |
| MetaMask shows wrong network | Add custom network: RPC `http://127.0.0.1:7545`, Chain ID `1337` |
| Python venv broken | Delete `backend/venv` folder and re-run `python -m venv backend/venv` |
| Contracts won't compile | Run `npm install` to get OpenZeppelin, then `truffle compile` |
| `invalid opcode` error on Ganache | Make sure Solidity version is 0.8.21 in `truffle-config.js` |

---

## License

MIT
