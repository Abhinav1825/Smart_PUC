# SmartPUC — Demo & Training Guide

> A step-by-step guide for demonstrating and using every feature of the SmartPUC platform.

---

## Table of Contents

1. [Quick Start (One Command)](#1-quick-start)
2. [System Overview](#2-system-overview)
3. [Page-by-Page Walkthrough](#3-page-by-page-walkthrough)
   - [3.1 Vehicle Dashboard (index.html)](#31-vehicle-dashboard)
   - [3.2 Authority Dashboard (authority.html)](#32-authority-dashboard)
   - [3.3 Analytics Dashboard (analytics.html)](#33-analytics-dashboard)
   - [3.4 Public Verification Portal (verify.html)](#34-public-verification-portal)
   - [3.5 Fleet Management (fleet.html)](#35-fleet-management)
   - [3.6 RTO Integration Portal (rto.html)](#36-rto-integration-portal)
   - [3.7 GCT Marketplace (marketplace.html)](#37-gct-marketplace)
   - [3.8 Vehicle Comparison (compare.html)](#38-vehicle-comparison)
   - [3.9 CPCB Public Dashboard (cpcb.html)](#39-cpcb-public-dashboard)
   - [3.10 Blockchain Explorer (explorer.html)](#310-blockchain-explorer)
   - [3.11 Fleet Leaderboard (leaderboard.html)](#311-fleet-leaderboard)
   - [3.12 Digital Twin (twin.html)](#312-digital-twin)
4. [Demo Scenarios](#4-demo-scenarios)
5. [Key Talking Points for Each Audience](#5-key-talking-points)
6. [Troubleshooting](#6-troubleshooting)

---

## 1. Quick Start

### One-Command Launch

```bash
python scripts/start_demo.py
```

This single command:
1. Starts a local Hardhat blockchain node
2. Deploys all 3 smart contracts (GreenToken, EmissionRegistry, PUCCertificate)
3. Seeds the database with demo data for 4 vehicles
4. Starts the FastAPI backend on port 5000
5. Starts the frontend server on port 3000
6. Opens your browser automatically

**Wait ~60 seconds** for contract deployment to complete. Do not press Ctrl+C during this time.

### Manual Launch (if needed)

Open 4 terminals:

```bash
# Terminal 1: Blockchain
npx hardhat node

# Terminal 2: Deploy (after node starts)
npx hardhat run scripts/deploy.js --network localhost

# Terminal 3: Backend
python -m uvicorn backend.main:app --host 0.0.0.0 --port 5000

# Terminal 4: Frontend
npx http-server frontend -p 3000 -c-1 --cors
```

### URLs

| Service  | URL |
|----------|-----|
| Frontend | http://localhost:3000 |
| Backend API | http://localhost:5000 |
| API Docs | http://localhost:5000/docs |
| Hardhat Node | http://127.0.0.1:8545 |

### Demo Vehicles Pre-loaded

| Vehicle ID | Model | Profile |
|------------|-------|---------|
| MH12AB1234 | Maruti Ciaz | Clean — all PASS, CES 0.4-0.7 |
| MH01CD5678 | Maruti WagonR | Degraded — some FAILs, CES 0.8-1.2 |
| MH04EF9012 | Tata Nexon (Diesel) | Near-threshold, CES ~0.9-1.05 |
| MH02GH3456 | Hyundai i20 | Fraud alerts — elevated fraud scores |

---

## 2. System Overview

SmartPUC is a **3-node blockchain-anchored vehicle emission compliance system**:

```
 Node 1: OBD-II Device          Node 2: Testing Station       Node 3: Blockchain
 (Vehicle sensor data)          (Backend processing)           (Immutable storage)
                                                               
 [ELM327 Dongle]                [FastAPI Backend]              [EmissionRegistry]
       |                              |                              |
       | OBD-II PIDs             VSP Model                    Store emission
       | (speed, RPM,           Emission Engine                records on-chain
       |  fuel rate)            Fraud Detector                       |
       +--------------------->  LSTM Predictor             [PUCCertificate NFT]
            POST /api/record         |                     Issue/revoke PUC
                                     |                     certificates
                                [SQLite DB]                       |
                                 Cold-path                  [GreenToken ERC-20]
                                 storage                    Reward compliant
                                     |                      vehicles
                                [12-Page Frontend]
                                 Real-time dashboard
```

### What It Does

1. **Collects** OBD-II sensor data (speed, RPM, fuel rate, temperature)
2. **Calculates** 5 pollutant emissions (CO2, CO, NOx, HC, PM2.5) using EPA MOVES methodology
3. **Computes** a Composite Emission Score (CES) — a single number for multi-pollutant compliance
4. **Detects fraud** using a 4-component ML ensemble (physics + Isolation Forest + temporal + drift)
5. **Stores** results on Ethereum blockchain (tamper-proof)
6. **Issues** PUC certificates as NFTs with tiered validity
7. **Rewards** clean vehicles with GreenTokens (ERC-20)

---

## 3. Page-by-Page Walkthrough

### 3.1 Vehicle Dashboard

**URL:** http://localhost:3000/index.html

This is the **main page** — the vehicle owner's real-time emission monitor.

#### What You See

- **Top bar**: Wallet connection, language selector (EN/HI/MR), theme toggle, notifications
- **Vehicle selector**: Dropdown with all registered vehicles
- **Live metrics**: 8 gauges showing real-time RPM, Speed, Fuel Rate, CO2, CO, NOx, HC, PM2.5
- **CES gauge**: Large circular gauge showing the Composite Emission Score
- **WLTC phase dots**: Shows which driving phase is active (Low/Medium/High/Extra High)
- **LSTM prediction chart**: 5-second emission forecast
- **Map**: Mumbai route with animated vehicle marker
- **Emission history table**: Past readings with fraud scores
- **Vehicle Health Forecast**: Months until PUC failure, catalyst health %

#### How to Demo

1. **Select a vehicle** from the dropdown (start with MH12AB1234 — the clean vehicle)
2. **Click "Start Route"** — the car begins moving on the map along a Mumbai route
3. Watch the **gauges update in real-time** as speed/acceleration change
4. Point out the **CES gauge** — it stays green (< 1.0) for the clean vehicle
5. **Switch to MH01CD5678** (degraded vehicle) — CES jumps to yellow/red
6. **Switch to MH02GH3456** (fraud vehicle) — the **red fraud alert banner** appears
7. Show the **emission history table** scrolling with new entries
8. Show the **Vehicle Health Forecast** card — months until failure, catalyst health
9. Click **"Request Certificate"** if the vehicle is eligible (3+ consecutive passes)

#### Key Talking Points

- "Real-time multi-pollutant monitoring — not just CO2"
- "CES catches violations that a CO2-only system would miss"
- "Fraud detection runs on every single reading"
- "Everything is stored on blockchain — tamper-proof"

---

### 3.2 Authority Dashboard

**URL:** http://localhost:3000/authority.html

This is the **testing station admin panel** — for RTO officers and PUC center operators.

#### What You See

- **6 metric cards**: Total Records, Violations, Fraud Alerts, Vehicles, Compliance %, Avg CES
- **Issue Certificate**: Enter vehicle ID + owner wallet address to mint a PUC NFT
- **Revoke Certificate**: Enter token ID + reason to revoke a certificate
- **Device Management**: Register/deregister OBD devices
- **Station Management**: Authorize/deauthorize testing stations
- **Vehicle Lookup**: VAHAN integration showing registration details
- **Records table**: All emission records with filter (All/Violations/Fraud)

#### How to Demo

1. Show the **overview metrics** — "This station has processed X records with Y violations"
2. **Issue a certificate**: Type `MH12AB1234` and a wallet address, click "Issue"
3. Show the **records table** — filter to "Violations Only" to show non-compliant readings
4. Filter to **"Fraud Alerts"** — show readings flagged by the ML ensemble
5. **Look up a vehicle** — type a registration number, show VAHAN data integration
6. Explain: "Only authorized stations can write to the blockchain"

---

### 3.3 Analytics Dashboard

**URL:** http://localhost:3000/analytics.html

Fleet-wide emission analytics and trends.

#### What You See

- **Fleet overview cards**: Total Vehicles, Avg CES, Compliance Rate, Total Violations
- **Emission trends line chart**: CES over time for selected vehicle
- **CES distribution histogram**: How many vehicles fall in each CES bucket
- **WLTC phase breakdown**: Doughnut chart showing emission distribution by driving phase
- **Worst performers table**: Top 10 highest-CES vehicles
- **Export buttons**: CSV, LaTeX, HTML report

#### How to Demo

1. Show the **fleet compliance rate** — "88% of vehicles are currently compliant"
2. Show the **CES distribution** — "Most vehicles cluster in the 0.3-0.6 range"
3. Point out the **worst performers** — "These 10 vehicles need immediate attention"
4. Click **"Export LaTeX"** — "Publication-ready tables for our IEEE paper"
5. Click **"Export CSV"** — "Full data export for offline analysis"
6. Show the **WLTC phase breakdown** — "Extra High phase produces the most emissions"

---

### 3.4 Public Verification Portal

**URL:** http://localhost:3000/verify.html

**Public-facing** — anyone can verify a vehicle's PUC status.

#### What You See

- **Search bar**: Enter a registration number
- **Certificate status**: Valid/Invalid/Expired with dates
- **Vehicle specs**: From VAHAN database
- **Emission summary**: Last test results with all 5 pollutants
- **QR code**: Scannable verification link
- **Blockchain proof**: Transaction hash and block number

#### How to Demo

1. Type `MH12AB1234` and click **"Verify"**
2. Show the **green "VALID" badge** with issue and expiry dates
3. Point to the **QR code** — "Any traffic police officer can scan this"
4. Show the **blockchain proof** — "Certificate is anchored on-chain, cannot be forged"
5. Click **"Download PDF"** — show the printable certificate with all pollutant readings

---

### 3.5 Fleet Management

**URL:** http://localhost:3000/fleet.html

Multi-vehicle fleet management for transport companies.

#### What You See

- **Fleet overview cards**: Total Vehicles, Records, Compliance %, Avg CES, GCT Earned
- **Vehicle table**: Sortable by any column, with PUC status badges
- **Bulk actions**: Select multiple vehicles, issue certificates in batch
- **Active alerts panel**: Severity-coded alert list
- **Fleet comparison chart**: CES bar chart across all vehicles

#### How to Demo

1. Show the **sortable vehicle table** — click "Avg CES" to sort by compliance
2. **Select 2-3 vehicles** using checkboxes — show the "Bulk Issue" button
3. Point to **status badges**: Green (VALID), Yellow (EXPIRED), Red (REVOKED)
4. Show the **alerts panel** — "3 vehicles need attention"
5. Explain: "Fleet operators can manage hundreds of vehicles from one screen"

---

### 3.6 RTO Integration Portal

**URL:** http://localhost:3000/rto.html

For Regional Transport Office (RTO) enforcement officers.

#### What You See

- **Tab interface**: Compliance Check / Flagged Vehicles / Heatmap / Enforcement Log
- **Compliance check**: Enter vehicle ID, get a verdict (COMPLIANT/NON-COMPLIANT)
- **VAHAN integration**: Registration details pulled from government database
- **Flagged vehicles**: List of non-compliant vehicles requiring enforcement
- **Enforcement log**: Record of warnings, retest orders, inspections

#### How to Demo

1. Go to **Compliance Check** tab — enter `MH01CD5678` (the degraded vehicle)
2. Show the **"NON-COMPLIANT" verdict** in red
3. Switch to **Flagged Vehicles** tab — "These vehicles have been automatically flagged"
4. Switch to **Enforcement Log** — "Every enforcement action is timestamped"
5. Explain: "RTO officers get real-time data instead of 6-month-old paper certificates"

---

### 3.7 GCT Marketplace

**URL:** http://localhost:3000/marketplace.html

Green Credit Token reward system for compliant vehicles.

#### What You See

- **GCT balance**: Large display showing earned tokens
- **Reward catalog**: 4 reward types with costs
  - Toll Discount (50 GCT)
  - Parking Waiver (30 GCT)
  - Tax Credit (100 GCT)
  - Priority Service (20 GCT)
- **Transfer section**: Send GCT to another wallet
- **Redemption history**: Past reward claims

#### How to Demo

1. **Connect MetaMask wallet** to see the GCT balance
2. Show the **reward catalog** — "Clean vehicles earn tokens they can spend"
3. Click **"Redeem"** on Toll Discount — show the blockchain transaction
4. Explain: "This creates a positive incentive loop — cleaner vehicles save money"
5. Show the **transfer feature** — "Tokens are standard ERC-20, fully tradeable"

---

### 3.8 Vehicle Comparison

**URL:** http://localhost:3000/compare.html

Side-by-side comparison of multiple vehicles.

#### What You See

- **Vehicle selector grid**: Click to select vehicles for comparison
- **Specs comparison table**: Make, model, fuel type, BS standard, engine
- **Stats comparison**: Records, violations, avg CES, compliance rate
- **Timeline bars**: Visual pass/fail history
- **CES vs CO2-only comparison section**: Detection rate cards and per-pollutant breakdown

#### How to Demo

1. **Select MH12AB1234** (clean) and **MH01CD5678** (degraded)
2. Click **"Compare"** — show the side-by-side stats
3. Point to the **timeline bars** — "Green = PASS, Red = FAIL — you can see the pattern"
4. Scroll to the **CES vs CO2-only section** — this is key for the paper:
   - "CES detects **42.4%** of violations vs CO2-only's **29.8%**"
   - "That's **15,007 seconds** of missed violations that a single-pollutant system would miss"
   - "NOx is the biggest contributor to the gap"

---

### 3.9 CPCB Public Dashboard

**URL:** http://localhost:3000/cpcb.html

Read-only public dashboard for the Central Pollution Control Board.

#### What You See

- **8 metric cards**: Vehicles Monitored, BS-VI Pass Rate, Fraud Alerts (24h), Certs Issued, Stations Online, Fleet Avg CES, Total Violations, On-Chain Records
- **CES distribution chart**: Fleet-wide histogram
- **Geographic coverage**: Static map showing test station locations
- **Top polluters table**: Anonymized vehicle IDs (privacy-preserving)

#### How to Demo

1. Explain: "This is a read-only view — no login needed"
2. Point to **anonymized vehicle IDs** — "Vehicle identities are hashed for privacy (DPDP Act compliance)"
3. Show **auto-refresh** — "Updates every 60 seconds for live monitoring"
4. Explain: "CPCB can monitor fleet-wide compliance without accessing individual vehicle data"

---

### 3.10 Blockchain Explorer

**URL:** http://localhost:3000/explorer.html

Blockchain transaction browser and system health monitor.

#### What You See

- **Health dashboard**: Backend API, Blockchain, Fraud Detector, LSTM status indicators
- **Deployed contracts**: Contract addresses with copy buttons
- **Recent transactions**: List with type badges (Emission/Certificate/Token)
- **Transaction details**: Expandable rows with full data

#### How to Demo

1. Show the **health indicators** — all green means everything is running
2. **Copy a contract address** — explain "These are the on-chain smart contracts"
3. Show **recent transactions** — "Every emission record is a blockchain transaction"
4. Click a transaction to **expand details** — show the full emission data stored on-chain
5. Explain: "This is full transparency — anyone can audit the blockchain"

---

### 3.11 Fleet Leaderboard

**URL:** http://localhost:3000/leaderboard.html

Gamified compliance ranking across the fleet.

#### What You See

- **Leaderboard table**: Ranked by CES score (lowest = best)
- **Top 3 badges**: Gold, silver, bronze for the cleanest vehicles
- **CES bars**: Color-coded compliance visualization
- **Trend arrows**: Whether each vehicle is improving or declining
- **Pollution Cost Calculator**: Interactive penalty estimation tool

#### How to Demo

1. Show the **#1 vehicle** with the gold badge — "This is the cleanest vehicle in the fleet"
2. Point to **trend arrows** — "This vehicle is improving, this one is declining"
3. Use the **Cost Calculator**:
   - Select a vehicle
   - Adjust the **penalty rate slider** (Rs. 100-1000 per CES unit)
   - Adjust **readings per year**
   - Show the **estimated annual pollution penalty** — "This vehicle would cost Rs. X per year"
4. Explain: "Gamification encourages compliance — nobody wants to be at the bottom"

---

### 3.12 Digital Twin

**URL:** http://localhost:3000/twin.html

Virtual vehicle emission modeling and maintenance simulation.

#### What You See

- **Animated vehicle visualization**: SVG car with emission clouds
- **Live metric gauges**: RPM, Speed, Temperature, Fuel, Emissions
- **Health summary cards**: Engine, Emission Control, Fuel Efficiency, Overall
- **Maintenance comparison**: Before/After side-by-side (oil, spark plugs, air filter, catalytic converter)

#### How to Demo

1. Select a vehicle from the dropdown
2. Show the **animated emission clouds** — darker = more pollution
3. Point to **health cards** — "Engine health is at X%, emission control at Y%"
4. Show the **maintenance comparison** — "After replacing the catalytic converter, emissions dropped by 40%"
5. Explain: "Digital twin helps predict when maintenance will be needed"

---

## 4. Demo Scenarios

### Scenario A: "The Happy Path" (5 minutes)

Best for: General audience, first demo

1. Open **Vehicle Dashboard** (index.html)
2. Select **MH12AB1234** (clean vehicle)
3. Click **Start Route** — watch gauges come alive
4. Point out: CES stays green, all pollutants within limits
5. Open **Verify** page — verify the vehicle, show QR code
6. Open **Compare** page — show CES vs CO2-only detection gap

### Scenario B: "Catching the Cheater" (5 minutes)

Best for: Technical audience, security focus

1. Open **Vehicle Dashboard**
2. Select **MH02GH3456** (fraud vehicle) — **red fraud banner appears**
3. Explain the 4-component fraud detector (physics + IF + temporal + drift)
4. Open **Authority Dashboard** — filter to "Fraud Alerts"
5. Show that fraud score is stored on-chain (cannot be erased)
6. Open **Blockchain Explorer** — find the fraud transaction

### Scenario C: "Fleet Compliance" (5 minutes)

Best for: Fleet managers, transport companies

1. Open **Fleet Management** page
2. Show the **4 vehicles with different compliance levels**
3. Sort by Avg CES — worst performers at top
4. Open **Analytics** — show fleet compliance rate and worst performers
5. Open **Leaderboard** — show the ranking and cost calculator
6. Use cost calculator to show **"Rs. 45,000/year penalty for the worst vehicle"**

### Scenario D: "Regulator's View" (5 minutes)

Best for: Government officials, RTO officers

1. Open **RTO Portal** — check compliance for MH01CD5678 (degraded)
2. Show the **"NON-COMPLIANT" verdict**
3. Switch to **Flagged Vehicles** tab
4. Open **CPCB Dashboard** — show anonymized public view
5. Open **Verify** page — show the public verification portal
6. Explain: "Every certificate is an NFT on blockchain — cannot be forged"

### Scenario E: "The Full IEEE Paper Demo" (10 minutes)

Best for: Academic reviewers, conference presentations

1. **Architecture**: Show the system diagram in README
2. **Multi-pollutant**: Dashboard showing all 5 pollutants (not just CO2)
3. **CES advantage**: Compare page — 42.4% vs 29.8% detection rate
4. **Fraud detection**: Switch to fraud vehicle, show red banner, F1=0.92
5. **Blockchain**: Explorer page — show on-chain records, contract addresses
6. **NFT certificates**: Verify page — show QR code, PDF certificate
7. **Incentives**: Marketplace — show GreenToken rewards
8. **Scalability**: Analytics — show fleet-wide statistics
9. **Indian context**: RTO portal, VAHAN integration, BSVI thresholds
10. **Privacy**: CPCB page — anonymized vehicle IDs, DPDP Act compliance

---

## 5. Key Talking Points for Each Audience

### For IEEE Reviewers

- "CES detects 42.4% violations vs 29.8% for CO2-only — a 12.6pp improvement"
- "Fraud detection F1 = 0.92 with a 4-component ensemble"
- "Smart contracts enforce compliance logic on-chain (86 tests, all passing)"
- "Full WLTC and MIDC driving cycle support for Indian regulatory context"
- "All mathematical models cite EPA MOVES3, COPERT 5, and Rakha et al."

### For RTO Officers

- "Real-time monitoring instead of 6-month paper certificates"
- "Fraud detection catches impossible OBD readings automatically"
- "Digital certificates on blockchain — cannot be forged or duplicated"
- "VAHAN database integration for instant vehicle verification"
- "Automatic flagging of non-compliant vehicles"

### For ARAI Scientists

- "5-pollutant analysis: CO2, CO, NOx, HC, PM2.5 with BSVI thresholds"
- "Both WLTC and MIDC driving cycles implemented"
- "NOx Arrhenius temperature correction with Ea/R = 3500K"
- "Cold-start penalties per COPERT 5 (CO x1.80, HC x1.50)"
- "CES weights are author-proposed (0.35/0.30/0.15/0.12/0.08), not regulatory"

### For Car Manufacturers

- "Continuous monitoring catches degradation before it becomes critical"
- "Catalyst health prediction using COPERT 5 degradation model"
- "Digital twin simulation for maintenance planning"
- "Green token incentives for maintaining clean vehicles"
- "Supports BS-IV and BS-VI vehicles with separate threshold sets"

---

## 6. Troubleshooting

### "Port already in use"

```bash
# Kill existing processes
npx kill-port 5000 8545 3000
# Or on Windows:
netstat -ano | findstr :8545
taskkill /PID <pid> /F
```

### "Contract deployment is slow"

PUCCertificate deployment takes 15-30 seconds — this is normal for UUPS proxy contracts with `viaIR` compilation. Do not press Ctrl+C.

### "Dashboard shows empty gauges"

The dashboard auto-loads demo data on first visit. If it's still empty:
1. Check the backend is running: http://localhost:5000/api/status
2. Manually trigger: http://localhost:5000/api/simulate?vehicle_id=MH12AB1234

### "MetaMask not connecting"

1. Make sure MetaMask is installed
2. Add the Hardhat network: Chain ID 31337, RPC URL http://127.0.0.1:8545
3. Import a test account using the mnemonic: `myth like bonus scare over problem client lid proud cousin toddler paragraph`

### "Blockchain offline" message

The backend works without a blockchain node — it queues records for later. To connect:
1. Start the Hardhat node: `npx hardhat node`
2. Deploy contracts: `npx hardhat run scripts/deploy.js --network localhost`
3. Restart the backend

---

*Guide last updated: 2026-04-08*
