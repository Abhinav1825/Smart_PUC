@echo off
title Smart PUC - Project Launcher
color 0A
echo.
echo  ========================================================
echo   Smart PUC - Multi-Pollutant Vehicle Emission Monitor
echo   Blockchain + ML + WLTC + BSVI Compliance System
echo  ========================================================
echo.

:: ──────────────────────────────────────────────────────────
:: STEP 1: Install global tools (Truffle + Ganache)
:: ──────────────────────────────────────────────────────────
echo [1/7] Checking global tools...
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
echo [2/7] Installing Node.js dependencies (OpenZeppelin, HDWallet, http-server)...
call npm.cmd install

:: ──────────────────────────────────────────────────────────
:: STEP 3: Setup Python virtual environment + packages
:: ──────────────────────────────────────────────────────────
echo [3/7] Setting up Python environment...
if not exist "backend\venv\Scripts\python.exe" (
    echo       Creating virtual environment...
    cd backend
    python -m venv venv
    cd ..
)
echo       Installing Python packages (Flask, Web3, numpy, scikit-learn)...
call backend\venv\Scripts\pip.exe install -q -r requirements.txt

:: ──────────────────────────────────────────────────────────
:: STEP 4: Create .env configuration
:: ──────────────────────────────────────────────────────────
echo [4/7] Configuring environment...
(
echo RPC_URL=http://127.0.0.1:7545
echo PRIVATE_KEY=0x4f3edf983ac636a65a842ce7c78d9aa706d3b113bce9c46f30d7d21715b23b1d
echo FLASK_PORT=5000
echo FLASK_DEBUG=true
echo DEFAULT_VEHICLE_ID=MH12AB1234
echo CORS_ORIGINS=http://127.0.0.1:3000,http://localhost:3000,http://127.0.0.1:8080,http://localhost:8080
echo RATE_LIMIT_MAX=120
) > .env
echo       .env file created.

:: ──────────────────────────────────────────────────────────
:: STEP 5: Start Ganache (local blockchain)
:: ──────────────────────────────────────────────────────────
echo [5/7] Starting Ganache local blockchain on port 7545...
start "SmartPUC - Ganache Blockchain" /min cmd /c "ganache -d -p 7545"
echo       Waiting for Ganache to start...
timeout /t 5 /nobreak > nul

:: ──────────────────────────────────────────────────────────
:: STEP 6: Compile and deploy smart contracts
:: ──────────────────────────────────────────────────────────
echo [6/7] Compiling and deploying smart contracts...
call truffle migrate --reset --network development

:: ──────────────────────────────────────────────────────────
:: STEP 7: Start Backend + Frontend
:: ──────────────────────────────────────────────────────────
echo [7/7] Starting servers...
start "SmartPUC - Backend API (port 5000)" /min cmd /c "cd backend && venv\Scripts\python.exe app.py"
timeout /t 3 /nobreak > nul
start "SmartPUC - Frontend (port 3000)" /min cmd /c "npx http-server frontend -p 3000 -c-1 --cors"
timeout /t 2 /nobreak > nul

:: ──────────────────────────────────────────────────────────
:: DONE
:: ──────────────────────────────────────────────────────────
echo.
echo  ========================================================
echo   ALL SERVICES RUNNING
echo  ========================================================
echo.
echo   Dashboard:   http://127.0.0.1:3000
echo   Backend API: http://127.0.0.1:5000
echo   Blockchain:  http://127.0.0.1:7545
echo.
echo   MetaMask Setup:
echo     Network:   Ganache
echo     RPC URL:   http://127.0.0.1:7545
echo     Chain ID:  1337
echo     Import key (Account 2):
echo     0x6cbed15c793ce57650b9877cf6fa156fbef513c4e6134f022a85b1ffdd59b2a1
echo.
echo   Press any key to open the dashboard in your browser...
pause > nul
start http://127.0.0.1:3000
echo.
echo   Services are running in background windows.
echo   Close this window when done. To stop all services,
echo   close the Ganache, Backend, and Frontend windows.
echo.
pause
