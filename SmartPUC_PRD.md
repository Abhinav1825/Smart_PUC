**PROJECT REQUIREMENTS DOCUMENT**

**Blockchain-Based Real-Time Vehicle**

**Emission Monitoring and Compliance System**

**(Smart PUC)**

  ----------------------------------- ---------------------------------------------
  Document Version                    v1.0

  Status                              Draft

  Date                                March 30, 2026

  Classification                      Academic Project

  Domain                              Blockchain + IoT + Environmental Compliance
  ----------------------------------- ---------------------------------------------

*Prepared for Academic Laboratory Evaluation*

**1. Executive Summary**

The Smart PUC system is a next-generation Blockchain-based Real-Time Vehicle Emission Monitoring and Compliance platform designed to replace India\'s traditional Pollution Under Control (PUC) certificate framework. The existing system relies on periodic, manual vehicle testing and is susceptible to manipulation, data tampering, and fraud.

This project introduces a continuous, tamper-proof, and transparent emission monitoring framework by combining On-Board Diagnostics (OBD-II) data simulation, real-time emission calculation engines, smart contract-based compliance evaluation on Ethereum, and a full-stack decentralized application (DApp) interface for vehicle owners and regulatory authorities.

The system is structured across seven sequential phases and six laboratory experiments, each mapping to a core functional component. Upon completion, the system will be deployed on the Sepolia Ethereum testnet and will be publicly verifiable on Etherscan.

**2. Project Overview**

**2.1 Problem Statement**

The current PUC regime in India operates on a periodic manual inspection model with the following well-documented deficiencies:

-   No real-time emission tracking between PUC renewal cycles

-   High susceptibility to fraudulent certificate issuance

-   Centralised, mutable records prone to data manipulation

-   No automated enforcement mechanism upon threshold breach

-   Lack of transparency for vehicle owners, authorities, and the public

**2.2 Proposed Solution**

The Smart PUC system addresses these problems through:

-   Real-time OBD-II data simulation (speed, RPM, fuel consumption)

-   On-the-fly CO2 emission calculation using standard automotive formulae

-   Immutable blockchain storage of all emission records via Ethereum smart contracts

-   Automated PASS/FAIL compliance evaluation without human intervention

-   Dual-view dashboard: Vehicle Owner View and Authority View

-   Public verifiability of all records through Etherscan (Sepolia testnet)

**2.3 Project Scope**

  ---------------------------------------------------------------------------------------------------
  **Scope Area**            **Included**                   **Excluded**
  ------------------------- ------------------------------ ------------------------------------------
  Emission Data Source      OBD-II simulation (software)   Physical hardware OBD dongle integration

  Blockchain Network        Ethereum (Ganache + Sepolia)   Hyperledger Fabric (comparison only)

  Smart Contract Language   Solidity                       Vyper / Rust

  Frontend                  Web dashboard (React/HTML)     Mobile application

  Data Storage              On-chain (Ethereum)            IPFS / off-chain databases

  Deployment                Sepolia testnet                Ethereum mainnet
  ---------------------------------------------------------------------------------------------------

**3. Stakeholder Analysis**

  ---------------------------------------------------------------------------------------------------------
  **Stakeholder**          **Role**                **Primary Interest**
  ------------------------ ----------------------- --------------------------------------------------------
  Vehicle Owners           End User                View real-time emission stats, PASS/FAIL status

  Regulatory Authorities   Supervisor / Enforcer   Access historical records, violation logs, enforcement

  Traffic Police / RTO     Field Enforcer          Verify compliance status on-the-spot via blockchain

  Environmental Agencies   Policy Monitor          Aggregate emission data for policy decisions

  Academic Evaluators      Assessment              Verify experiment completion and DApp functionality

  Development Team         Builder                 Implement, test, and deploy all system components
  ---------------------------------------------------------------------------------------------------------

**4. Functional Requirements**

**4.1 OBD-II Data Simulation Module**

  -----------------------------------------------------------------------------------------------------------------------------------------------
  **ID**   **Requirement**                                                                                                         **Priority**
  -------- ----------------------------------------------------------------------------------------------------------------------- --------------
  FR-01    System shall simulate real-time vehicle telemetry: engine RPM (600--4000), speed (0--120 km/h), fuel consumption rate   High

  FR-02    Simulator shall output data at configurable intervals (default: every 5 seconds)                                        High

  FR-03    Simulated data shall reflect realistic driving patterns including idle, city, and highway modes                         Medium

  FR-04    Simulator shall expose data via a REST API endpoint consumable by the emission engine                                   High
  -----------------------------------------------------------------------------------------------------------------------------------------------

**4.2 Emission Calculation Engine**

  ------------------------------------------------------------------------------------------------------------------------
  **ID**   **Requirement**                                                                                  **Priority**
  -------- ------------------------------------------------------------------------------------------------ --------------
  FR-05    Engine shall compute CO2 emissions using the formula: CO2 = Fuel_Consumption x Emission_Factor   High

  FR-06    Engine shall accept RPM, speed, and fuel rate as inputs and return g/km CO2 output               High

  FR-07    Engine shall apply ARAI/EURO standard emission factors for petrol and diesel fuel types          Medium

  FR-08    Engine shall expose a Python-based API (Flask/FastAPI) for backend consumption                   High
  ------------------------------------------------------------------------------------------------------------------------

**4.3 Blockchain & Smart Contract**

  --------------------------------------------------------------------------------------------------------------------------------
  **ID**   **Requirement**                                                                                          **Priority**
  -------- -------------------------------------------------------------------------------------------------------- --------------
  FR-09    Smart contract (EmissionContract.sol) shall store: vehicle ID, timestamp, CO2 value, compliance status   High

  FR-10    Contract shall evaluate emissions against a configurable threshold and auto-assign PASS or FAIL          High

  FR-11    Contract shall emit a Solidity Event on every FAIL outcome for real-time alert triggering                High

  FR-12    Contract shall be compiled using Truffle and deployed to local Ganache and Sepolia testnet               High

  FR-13    Contract functions shall be callable via Web3.py (backend) and Web3.js/Ethers.js (frontend)              High

  FR-14    All records stored on-chain shall be immutable and timestamped                                           High
  --------------------------------------------------------------------------------------------------------------------------------

**4.4 Backend API**

  ------------------------------------------------------------------------------------------------------------------------------------
  **ID**   **Requirement**                                                                                              **Priority**
  -------- ------------------------------------------------------------------------------------------------------------ --------------
  FR-15    Backend shall orchestrate: data fetch from simulator → emission calculation → blockchain write               High

  FR-16    Backend shall expose REST endpoints for frontend consumption (GET emission history, GET compliance status)   High

  FR-17    Backend shall use Web3.py to interact with the deployed smart contract                                       High

  FR-18    Backend shall handle Ethereum transaction signing using loaded private keys (from .env)                      High
  ------------------------------------------------------------------------------------------------------------------------------------

**4.5 Frontend Dashboard**

  -------------------------------------------------------------------------------------------------------------------------------
  **ID**   **Requirement**                                                                                         **Priority**
  -------- ------------------------------------------------------------------------------------------------------- --------------
  FR-19    Vehicle Owner Dashboard shall display: live RPM, speed, CO2 level, and PASS/FAIL badge                  High

  FR-20    Authority Dashboard shall display: all vehicle records, timestamps, historical trends, violation list   High

  FR-21    Frontend shall connect to blockchain via MetaMask wallet for transaction signing                        High

  FR-22    Frontend shall use Web3.js or Ethers.js to read on-chain data in real-time                              High

  FR-23    UI shall auto-refresh emission data without requiring page reload                                       Medium
  -------------------------------------------------------------------------------------------------------------------------------

**4.6 Wallet & Transaction Management**

  -----------------------------------------------------------------------------------------------------------------
  **ID**   **Requirement**                                                                           **Priority**
  -------- ----------------------------------------------------------------------------------------- --------------
  FR-24    MetaMask shall be used as the primary wallet for DApp interaction                         High

  FR-25    MetaMask shall support connection to Ganache (local) and Sepolia (testnet) networks       High

  FR-26    All blockchain write operations shall require wallet confirmation (transaction signing)   High
  -----------------------------------------------------------------------------------------------------------------

**5. Non-Functional Requirements**

  -----------------------------------------------------------------------------------------------------------
  **Category**    **ID**   **Requirement**
  --------------- -------- ----------------------------------------------------------------------------------
  Performance     NFR-01   Emission data shall be written to blockchain within 10 seconds of generation

  Performance     NFR-02   Frontend dashboard shall refresh within 3 seconds of a new on-chain record

  Security        NFR-03   No private keys shall be hardcoded; all secrets stored in .env files

  Security        NFR-04   Smart contract shall have input validation to reject malformed emission values

  Reliability     NFR-05   System shall handle RPC connection failures gracefully with error messages

  Scalability     NFR-06   Smart contract design shall support multiple vehicles without structural changes

  Usability       NFR-07   Dashboard UI shall be operable without blockchain expertise by end users

  Verifiability   NFR-08   All testnet transactions shall be publicly verifiable on Etherscan (Sepolia)

  Compliance      NFR-09   Emission thresholds shall align with EURO 6 / Bharat Stage VI standards
  -----------------------------------------------------------------------------------------------------------

**6. System Architecture**

**6.1 High-Level Architecture**

The system follows a layered DApp architecture with the following tiers:

  -----------------------------------------------------------------------------------
  **Layer**         **Component**                 **Technology**
  ----------------- ----------------------------- -----------------------------------
  Data Generation   OBD-II Simulator              Python (random/threading)

  Processing        Emission Calculation Engine   Python (Flask API)

  Persistence       Ethereum Smart Contract       Solidity + Truffle

  Middleware        Backend Orchestrator          Python + Web3.py

  Presentation      Web Dashboard                 HTML/CSS/JS or React + Ethers.js

  Wallet            Transaction Signer            MetaMask

  Testnet           Public Deployment             Infura/Alchemy + Sepolia
  -----------------------------------------------------------------------------------

**6.2 Data Flow**

The end-to-end data flow is as follows:

1.  OBD-II Simulator generates telemetry (RPM, Speed, Fuel Rate) every 5 seconds

2.  Emission Engine receives telemetry and computes CO2 in g/km

3.  Backend API packages the result and signs a blockchain transaction via Web3.py

4.  Smart Contract (EmissionContract.sol) receives the transaction, evaluates compliance, and stores the record

5.  Smart Contract emits a Violation Event if the FAIL threshold is exceeded

6.  Frontend Dashboard reads on-chain data via Web3.js/Ethers.js and updates the UI

7.  MetaMask handles all transaction signing and network switching for end users

**6.3 Project Directory Structure**

smart-puc/

  ----------------------------------------------------------------------------------
  **Directory / File**              **Purpose**
  --------------------------------- ------------------------------------------------
  contracts/EmissionContract.sol    Solidity smart contract -- core business logic

  migrations/2_deploy_contract.js   Truffle deployment script

  truffle-config.js                 Network configuration (Ganache + Sepolia)

  backend/simulator.py              OBD-II data simulator

  backend/emission_engine.py        CO2 calculation logic

  backend/blockchain_connector.py   Web3.py interaction layer

  backend/app.py                    Flask/FastAPI REST API

  frontend/index.html               Vehicle Owner Dashboard

  frontend/authority.html           Authority Dashboard

  frontend/app.js                   Web3.js / Ethers.js frontend logic

  .env                              Infura API key, wallet mnemonic (gitignored)

  test/TestEmission.js              Truffle unit tests for smart contract
  ----------------------------------------------------------------------------------

**7. Phase-Wise Implementation Plan**

  -------------------------------------------------------------------------------------------------------------------------
  **Phase**   **Name**                           **Maps To**           **Key Deliverable**
  ----------- ---------------------------------- --------------------- ----------------------------------------------------
  Phase 1     Local Blockchain Setup             Experiment 1          Ganache running, Truffle project initialized

  Phase 2     Smart Contract Development         Experiment 2          EmissionContract.sol compiled and deployed locally

  Phase 3     Backend + Blockchain Integration   Experiment 3 (Part)   Python backend writes emission data to blockchain

  Phase 4     Testnet Deployment                 Experiment 3 (Full)   Contract live on Sepolia, verified on Etherscan

  Phase 5     Wallet Integration                 Experiment 4          MetaMask connected to Ganache and Sepolia

  Phase 6     Frontend Dashboard                 Experiment 5/6        Working UI with real-time on-chain data

  Phase 7     Full DApp Integration              Experiment 6          All components integrated; end-to-end working DApp
  -------------------------------------------------------------------------------------------------------------------------

**Phase 1: Local Blockchain Setup**

-   Install Node.js (v18+), NPM, Truffle (npm install -g truffle), Ganache

-   Run: mkdir smart-puc && cd smart-puc && truffle init

-   Launch Ganache -- obtain 10 test accounts with private keys

-   Verify Truffle connects to Ganache on port 7545

**Phase 2: Smart Contract Development**

-   Create /contracts/EmissionContract.sol with functions: storeEmission(), checkCompliance(), getRecord()

-   Define threshold constant (e.g., 120 g/km CO2)

-   Implement Solidity Event: ViolationDetected(vehicleId, co2Level, timestamp)

-   Run truffle compile -- verify no errors

-   Create migrations/2_deploy_contract.js and run truffle migrate

**Phase 3: Backend Integration**

-   Implement simulator.py: generate RPM (600-4000), speed (0-120), fuel_rate (3-15 L/100km)

-   Implement emission_engine.py: CO2 = fuel_rate x 2.31 (petrol) or 2.68 (diesel) x distance_factor

-   Implement blockchain_connector.py using Web3.py to call storeEmission()

-   Test end-to-end: simulator → engine → contract

**Phase 4: Testnet Deployment**

-   Run: npm install \@truffle/hdwallet-provider dotenv

-   Register on Infura or Alchemy; obtain Sepolia RPC URL

-   Configure truffle-config.js with Sepolia network block

-   Fund deployment wallet with Sepolia ETH from faucet

-   Run: truffle migrate \--network sepolia

-   Verify contract address on https://sepolia.etherscan.io

**Phase 5: Wallet Integration**

-   Install MetaMask browser extension

-   Add Ganache custom network (RPC: http://127.0.0.1:7545, Chain ID: 1337)

-   Import Ganache account using private key

-   Switch to Sepolia testnet and verify wallet balance

**Phase 6: Frontend Dashboard**

-   Build Vehicle Dashboard: live emission gauge, speed/RPM display, PASS/FAIL badge (green/red)

-   Build Authority Dashboard: table of all vehicle records, violation filter, Etherscan link per transaction

-   Connect frontend using Ethers.js: provider = new ethers.BrowserProvider(window.ethereum)

-   Implement event listener for ViolationDetected to trigger real-time alerts

**Phase 7: Full DApp Integration**

-   Run all components simultaneously and verify complete data flow

-   Perform end-to-end test: simulate high emission → verify FAIL on chain → verify alert on UI

-   Record Sepolia contract address and transaction hashes for viva demonstration

**8. Smart Contract Specification**

**8.1 Contract: EmissionContract.sol**

  -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **Function**        **Visibility**     **Parameters**                                           **Returns**             **Description**
  ------------------- ------------------ -------------------------------------------------------- ----------------------- -----------------------------------------------------
  storeEmission()     public             vehicleId (string), co2 (uint256), timestamp (uint256)   void                    Stores emission record; triggers compliance check

  checkCompliance()   internal           co2 (uint256)                                            bool                    Returns true if CO2 \<= threshold; false otherwise

  getRecord()         public view        vehicleId (string), index (uint)                         EmissionRecord struct   Returns stored emission record by vehicle and index

  getViolations()     public view        vehicleId (string)                                       EmissionRecord\[\]      Returns all FAIL records for a vehicle

  setThreshold()      public onlyOwner   threshold (uint256)                                      void                    Updates the CO2 compliance threshold (owner only)
  -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**8.2 Events**

  -------------------------------------------------------------------------------------------------------
  **Event**           **Parameters**                      **Trigger Condition**
  ------------------- ----------------------------------- -----------------------------------------------
  ViolationDetected   vehicleId, co2Level, timestamp      CO2 exceeds threshold on storeEmission() call

  RecordStored        vehicleId, recordIndex, timestamp   Every successful emission record storage
  -------------------------------------------------------------------------------------------------------

**8.3 State Variables**

  ----------------------------------------------------------------------------------------------------------------------
  **Variable**      **Type**                                 **Default**   **Description**
  ----------------- ---------------------------------------- ------------- ---------------------------------------------
  threshold         uint256                                  120           CO2 limit in g/km (Bharat Stage VI aligned)

  owner             address                                  deployer      Contract owner for admin functions

  emissionRecords   mapping(string =\> EmissionRecord\[\])   ---           Vehicle ID → list of emission records
  ----------------------------------------------------------------------------------------------------------------------

**9. API Specification**

**9.1 Backend REST Endpoints**

  ---------------------------------------------------------------------------------------------------------------------------------------------------
  **Method**   **Endpoint**               **Description**                                           **Response**
  ------------ -------------------------- --------------------------------------------------------- -------------------------------------------------
  GET          /api/simulate              Triggers OBD-II simulation and returns latest telemetry   JSON: {rpm, speed, fuel_rate, co2}

  POST         /api/record                Calculates emission and writes to blockchain              JSON: {txHash, status, co2, compliance}

  GET          /api/history/{vehicleId}   Returns all on-chain emission records for vehicle         JSON: \[{co2, timestamp, status}\]

  GET          /api/violations            Returns all FAIL records across all vehicles              JSON: \[{vehicleId, co2, timestamp}\]

  GET          /api/status                Returns backend health and blockchain connection status   JSON: {connected, blockNumber, contractAddress}
  ---------------------------------------------------------------------------------------------------------------------------------------------------

**10. Testing Plan**

**10.1 Unit Tests -- Smart Contract**

  ------------------------------------------------------------------------------------------------------------------------------------
  **Test ID**   **Test Case**                                      **Expected Result**
  ------------- -------------------------------------------------- -------------------------------------------------------------------
  TC-01         Deploy EmissionContract and verify owner address   Owner matches deployer address

  TC-02         Store emission of 100 g/km (below 120 threshold)   Record stored with status = PASS

  TC-03         Store emission of 150 g/km (above 120 threshold)   Record stored with status = FAIL; ViolationDetected event emitted

  TC-04         Call getRecord() after storing 3 records           Returns all 3 records with correct values

  TC-05         Call setThreshold() from non-owner address         Transaction reverted with onlyOwner error

  TC-06         Deploy to Sepolia and call storeEmission()         Transaction confirmed; verifiable on Etherscan
  ------------------------------------------------------------------------------------------------------------------------------------

**10.2 Integration Tests**

  ---------------------------------------------------------------------------------------------------------------------
  **Test ID**   **Test Case**                                  **Expected Result**
  ------------- ---------------------------------------------- --------------------------------------------------------
  IT-01         Run simulator → engine → blockchain pipeline   CO2 value stored on-chain matches calculated value

  IT-02         MetaMask transaction sign on Ganache           Transaction confirmed; new record visible in dashboard

  IT-03         Frontend reads violation history               Authority dashboard displays all FAIL records

  IT-04         ViolationDetected event triggers UI alert      Alert banner appears within 5 seconds of FAIL record
  ---------------------------------------------------------------------------------------------------------------------

**11. Technology Stack**

  --------------------------------------------------------------------------------------------------------------------
  **Category**                 **Technology**                **Version**   **Purpose**
  ---------------------------- ----------------------------- ------------- -------------------------------------------
  Blockchain Platform          Ethereum                      ---           Decentralised ledger for emission records

  Smart Contract Language      Solidity                      \^0.8.0       EmissionContract development

  Development Framework        Truffle Suite                 \^5.x         Compile, test, migrate contracts

  Local Blockchain             Ganache                       \^7.x         Local Ethereum simulation (10 accounts)

  Backend Language             Python                        3.10+         OBD simulator, emission engine, API

  Blockchain Client (Python)   Web3.py                       \^6.x         Python → Ethereum interaction

  Web Framework                Flask / FastAPI               Latest        REST API server

  Frontend Framework           React or Vanilla JS           Latest        Dashboard UI

  Blockchain Client (JS)       Ethers.js                     \^6.x         Frontend → Ethereum interaction

  Wallet                       MetaMask                      Latest        Browser wallet for tx signing

  Testnet                      Sepolia                       ---           Public Ethereum testnet

  RPC Provider                 Infura / Alchemy              Latest        Testnet node access

  Node Runtime                 Node.js                       v18+          Truffle, NPM packages

  HD Wallet                    \@truffle/hdwallet-provider   Latest        Testnet deployment signing

  Env Management               dotenv                        Latest        Secure secret management
  --------------------------------------------------------------------------------------------------------------------

**12. Risk Register**

  -------------------------------------------------------------------------------------------------------------------------------------------------
  **Risk ID**   **Risk**                                **Likelihood**   **Impact**   **Mitigation**
  ------------- --------------------------------------- ---------------- ------------ -------------------------------------------------------------
  R-01          Sepolia faucet unavailable              Medium           High         Use Ganache for demo; document testnet TX hash if available

  R-02          Infura API rate limit hit               Low              Medium       Switch to Alchemy as fallback RPC provider

  R-03          MetaMask incompatibility with Ganache   Low              Medium       Manually configure network (RPC: 7545, Chain ID: 1337)

  R-04          Smart contract exceeds gas limit        Low              High         Optimise storage; use mappings over arrays where possible

  R-05          Web3.py version mismatch                Medium           Medium       Pin version in requirements.txt; test on clean virtualenv

  R-06          Frontend CORS errors with backend       Medium           Low          Enable Flask-CORS; configure allowed origins
  -------------------------------------------------------------------------------------------------------------------------------------------------

**13. Experiment-to-Deliverable Mapping**

The following table maps each lab experiment to its corresponding system component, providing a clear audit trail for examination:

  -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **Experiment**   **Title**                           **Phase**      **Key Deliverable**                        **Examiner Checkpoints**
  ---------------- ----------------------------------- -------------- ------------------------------------------ --------------------------------------------------------------------------
  Experiment 1     Local Blockchain Setup              Phase 1        Ganache + Truffle initialized              Show Ganache GUI with 10 accounts; show truffle init output

  Experiment 2     Smart Contract Development          Phase 2        EmissionContract.sol deployed locally      Show compiled ABI; show truffle migrate output; show contract on Ganache

  Experiment 3     Testnet Deployment                  Phases 3 & 4   Contract on Sepolia; backend interaction   Show Etherscan transaction; show Web3.py call logs

  Experiment 4     MetaMask & Wallet Integration       Phase 5        MetaMask connected to DApp                 Demonstrate transaction signing via MetaMask popup

  Experiment 5     Hyperledger Comparison (Optional)   ---            Comparison analysis document               Submit written comparison: Ethereum vs Hyperledger Fabric

  Experiment 6     Final DApp Integration              Phase 7        Fully working Smart PUC DApp               Live demo: generate emission → blockchain write → UI update
  -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**14. Glossary**

  -----------------------------------------------------------------------------------------------------------
  **Term**           **Definition**
  ------------------ ----------------------------------------------------------------------------------------
  PUC                Pollution Under Control -- Indian mandatory vehicle emission compliance certificate

  OBD-II             On-Board Diagnostics (version 2) -- standard vehicle self-diagnostic protocol

  DApp               Decentralised Application -- application running on blockchain infrastructure

  Smart Contract     Self-executing code stored on blockchain that automatically enforces rules

  ABI                Application Binary Interface -- interface definition for smart contract function calls

  Ganache            Local in-memory Ethereum blockchain for development and testing

  Sepolia            Public Ethereum test network (testnet) used for pre-mainnet deployment

  Truffle            Development framework for compiling, testing, and migrating Ethereum contracts

  Web3.py            Python library for interacting with Ethereum nodes and smart contracts

  Ethers.js          JavaScript library for Ethereum smart contract interaction in frontend applications

  MetaMask           Browser-based Ethereum wallet used to sign and submit transactions

  Infura/Alchemy     Hosted Ethereum node providers offering RPC access to testnets and mainnet

  g/km               Grams per kilometre -- standard unit for vehicle CO2 emission measurement

  BSVI               Bharat Stage VI -- India\'s current vehicle emission standards (equivalent to EURO 6)
  -----------------------------------------------------------------------------------------------------------

**15. Document Sign-Off**

  -----------------------------------------------------------------------------------------------------------------------------------------------
  **Role**                     **Name**                                 **Signature**                            **Date**
  ---------------------------- ---------------------------------------- ---------------------------------------- --------------------------------
  Project Lead / Developer     \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_   \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_   \_\_\_\_\_ / \_\_\_\_\_ / 2026

  Faculty Guide / Supervisor   \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_   \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_   \_\_\_\_\_ / \_\_\_\_\_ / 2026

  Internal Examiner            \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_   \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_   \_\_\_\_\_ / \_\_\_\_\_ / 2026
  -----------------------------------------------------------------------------------------------------------------------------------------------

*This document is intended for academic evaluation purposes only. All implementations described herein are to be demonstrated in a controlled lab environment.*
