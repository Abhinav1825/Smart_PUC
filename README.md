# Smart PUC — Real-Time Vehicle Emission Monitoring System

Blockchain-Based Real-Time Vehicle Emission Monitoring and Compliance System.
This project implements a complete, transparent, and tamper-proof alternative to traditional Pollution Under Control (PUC) certificates. It focuses on **petrol vehicles** with an emission threshold of 120 g/km CO₂ (aligned with Bharat Stage VI / EURO 6).

## Architecture

The system consists of three main layers:
1. **Smart Contract (Solidity)**: Deployed on Ethereum (local Ganache or Sepolia testnet) to store emission records immutably and emit violation events.
2. **Backend Engine (Python/Flask)**: Simulates OBD-II telemetry (RPM, Speed, Fuel Rate), calculates CO₂ emissions, and interacts with the blockchain via Web3.py.
3. **Frontend Dashboard (HTML/JS/Ethers.js)**: Provides role-based views for Vehicle Owners and Regulatory Authorities, integrating with MetaMask for wallet connection and real-time blockchain reads.

## Prerequisites

- **Node.js** (v18+) and **npm**
- **Python** (3.10+)
- **Ganache** (UI or CLI) for local blockchain simulation
- **MetaMask** browser extension
- **Truffle** (install globally via `npm install -g truffle`)

## Setup Instructions (All Phases)

### Phase 1: Local Blockchain Setup
1. Launch **Ganache** (Quickstart). Make sure it is running on `HTTP://127.0.0.1:7545` and Network ID `1337`.
2. Extract the first private key from Ganache to use for the backend.

### Phase 2: Smart Contract Setup
1. Open a terminal in the project root folder.
2. Install npm dependencies:
   ```bash
   npm install
   ```
3. Compile the smart contracts:
   ```bash
   npm run compile
   # or: truffle compile
   ```
4. Deploy to local Ganache:
   ```bash
   npm run migrate
   # or: truffle migrate --network development
   ```

### Phase 3: Backend & Environment Setup
1. Copy the environment variables template:
   ```bash
   cp .env.example .env
   ```
2. Edit `.env`:
   - Set `PRIVATE_KEY` to the private key of your first Ganache account (from Phase 1).
   - Keep `RPC_URL=http://127.0.0.1:7545`.
3. Set up a Python virtual environment and install dependencies:
   ```bash
   cd backend
   python -m venv venv
   # Windows: venv\\Scripts\\activate
   # Mac/Linux: source venv/bin/activate
   pip install -r ../requirements.txt
   ```
4. Start the backend API:
   ```bash
   python app.py
   ```
   The backend should report `✅ Connected` to the blockchain and start on port 5000.

### Phase 4 & 5: Wallet Integration & Frontend
1. Open your browser and configure **MetaMask**:
   - Add a custom network: Name = "Ganache", RPC URL = `http://127.0.0.1:7545`, Chain ID = `1337`.
   - Import an account using a private key from Ganache (preferably Account 2 or 3, different from the backend's).
2. Start the frontend development server:
   ```bash
   # Open a new terminal in the project root
   npm run dev:frontend
   ```
   (This runs `npx http-server frontend -p 8080 -c-1 --cors`)
3. Open `http://127.0.0.1:8080` in your browser.
4. Click **Connect Wallet** to connect MetaMask.

### Phase 6 & 7: Full DApp Interaction
1. **Vehicle Dashboard (`index.html`)**: Click "Simulate & Record". The backend will generate data, calculate CO₂, write it to the blockchain using the backend private key, and the frontend will update the metrics and PASS/FAIL status.
2. **Authority Dashboard (`authority.html`)**: Open this page to view all simulated emission records across all registered vehicles. You can filter by violations and observe real-time "Violation Detected" event alerts if someone records an emission over 120 g/km.

## Testnet Deployment (Sepolia) - Optional
1. Obtain an Infura/Alchemy Project ID and a MetaMask mnemonic with Sepolia test ETH.
2. Update `.env` with `INFURA_PROJECT_ID` and `MNEMONIC`.
3. Deploy: `npm run migrate:sepolia`
4. Update `RPC_URL` in `.env` to your Infura endpoint and restart the backend.
5. In MetaMask, switch to the Sepolia network.

## Running Tests
To run the Truffle smart contract tests:
```bash
truffle test
```
