@echo off
title Smart PUC - 3-Node Architecture Launcher
color 0A
echo.
echo  ============================================================
echo   Smart PUC - 3-Node Blockchain Emission Monitoring System
echo   EmissionRegistry + PUCCertificate (NFT) + GreenToken (ERC-20)
echo  ============================================================
echo.

:: ──────────────────────────────────────────────────────────
:: STEP 1: Install global tools (Truffle + Ganache)
:: ──────────────────────────────────────────────────────────
echo [1/8] Checking global tools...
where truffle >nul 2>nul
if %errorlevel% neq 0 (
    echo       Installing Truffle and Ganache globally...
    call npm.cmd install -g truffle ganache
) else (
    echo       Truffle and Ganache already installed.
)

:: ──────────────────────────────────────────────────────────
:: STEP 2: Install Node.js dependencies
:: ──────────────────────────────────────────────────────────
echo [2/8] Installing Node.js dependencies...
call npm.cmd install

:: ──────────────────────────────────────────────────────────
:: STEP 3: Setup Python virtual environment
:: ──────────────────────────────────────────────────────────
echo [3/8] Setting up Python environment...
if not exist "venv\Scripts\python.exe" (
    echo       Creating virtual environment...
    python -m venv venv
)
echo       Installing Python packages...
call venv\Scripts\pip.exe install -q -r requirements.txt

:: ──────────────────────────────────────────────────────────
:: STEP 4: Start Ganache (deterministic mode, 10 accounts)
:: ──────────────────────────────────────────────────────────
echo [4/8] Starting Ganache local blockchain on port 7545...
echo       Account roles: [0]=Admin  [1]=Station  [2]=Device  [3]=Owner
start "SmartPUC - Ganache Blockchain" /min cmd /c "ganache --deterministic --accounts 10 --defaultBalanceEther 100 --port 7545 --gasLimit 12000000"
echo       Waiting for Ganache to start...
timeout /t 5 /nobreak > nul

:: ──────────────────────────────────────────────────────────
:: STEP 5: Compile and deploy all 3 smart contracts
:: ──────────────────────────────────────────────────────────
echo [5/8] Deploying 3 smart contracts (EmissionRegistry + PUCCertificate + GreenToken)...
call truffle migrate --reset --network development

:: ──────────────────────────────────────────────────────────
:: STEP 6: Start Testing Station Backend (Node 2)
:: ──────────────────────────────────────────────────────────
echo [6/8] Starting Testing Station Backend (Node 2) on port 5000...
start "SmartPUC - Testing Station (Node 2)" /min cmd /c "venv\Scripts\python.exe backend\app.py"
timeout /t 3 /nobreak > nul

:: ──────────────────────────────────────────────────────────
:: STEP 7: Start Frontend Server (Node 3)
:: ──────────────────────────────────────────────────────────
echo [7/8] Starting Frontend (Node 3: Dashboards + Verification Portal) on port 3000...
start "SmartPUC - Frontend (Node 3)" /min cmd /c "npx http-server frontend -p 3000 -c-1 --cors"
timeout /t 2 /nobreak > nul

:: ──────────────────────────────────────────────────────────
:: STEP 8: Start OBD Device Simulator (Node 1) — optional
:: ────────────────────────────────────────────────────────��─
echo [8/8] Starting OBD Device Simulator (Node 1)...
start "SmartPUC - OBD Device (Node 1)" /min cmd /c "venv\Scripts\python.exe -m obd_node.obd_device --count 100 --interval 5"

:: ──────────────────────────────────────────────────────────
:: DONE
:: ──────────────────────────────────────────────────────────
echo.
echo  ============================================================
echo   ALL 3 NODES RUNNING
echo  ============================================================
echo.
echo   Node 1 (OBD Device):        Signing + sending telemetry
echo   Node 2 (Testing Station):   http://127.0.0.1:5000
echo   Node 3 (Frontend):          http://127.0.0.1:3000
echo   Blockchain (Ganache):       http://127.0.0.1:7545
echo.
echo   Dashboards:
echo     Vehicle Owner:     http://127.0.0.1:3000/index.html
echo     Authority Panel:   http://127.0.0.1:3000/authority.html
echo     Verification:      http://127.0.0.1:3000/verify.html
echo.
echo   MetaMask Setup:
echo     Network:    Ganache
echo     RPC URL:    http://127.0.0.1:7545
echo     Chain ID:   1337
echo.
echo     Import as Vehicle Owner (Account 3):
echo     0x646f1ce2fdad0e6deeeb5c7e8e5543bdde65e86029e2fd9fc169899c440a7913
echo.
echo   Press any key to open the dashboard...
pause > nul
start http://127.0.0.1:3000
echo.
echo   Services are running in background windows.
echo   Close this window when done.
echo.
pause
