@echo off
echo =======================================================
echo Smart PUC — One-Click Project Runner
echo =======================================================
echo.

:: 1. Setup Node Modules
echo [1/5] Installing npm dependencies...
call npm.cmd install

:: 2. Setup Python Virtual Environment and Dependencies
echo [2/5] Setting up Python virtual environment...
if not exist "backend\venv" (
    cd backend
    python -m venv venv
    cd ..
)
echo Installing backend requirements...
call backend\venv\Scripts\pip.exe install -r requirements.txt

:: 3. Create .env file with deterministic key
echo [3/5] Configuring environment variables...
echo RPC_URL=http://127.0.0.1:7545 > .env
echo PRIVATE_KEY=0x4f3edf983ac636a65a842ce7c78d9aa706d3b113bce9c46f30d7d21715b23b1d >> .env
echo FLASK_PORT=5000 >> .env
echo FLASK_DEBUG=true >> .env
echo DEFAULT_VEHICLE_ID=MH12AB1234 >> .env

:: 4. Start Ganache in a new window
echo [4/5] Starting Ganache Local Blockchain...
start "Ganache (Blockchain)" cmd /k "npx ganache -d -p 7545"

:: Wait for Ganache to start up completely...
echo Waiting 5 seconds for Ganache to start...
timeout /t 5 > nul

:: 5. Deploy Smart Contract
echo [5/5] Deploying Smart Contracts...
call npm.cmd run migrate

:: 6. Launch Backend and Frontend in new windows
echo.
echo =======================================================
echo Starting Servers...
echo =======================================================
start "Smart PUC - Backend Server" cmd /k "cd backend && venv\Scripts\python.exe app.py"
start "Smart PUC - Frontend Dashboard" cmd /k "npm.cmd run dev:frontend"

echo.
echo All services launched!
echo - Ganache is running on port 7545.
echo - Backend is running on port 5000.
echo - Frontend is opening on port 8080.
echo.
echo Please manually open your browser to: http://127.0.0.1:8080
echo To connect your MetaMask wallet, use the deterministic Ganache seed phrase:
echo "myth like bonus cause predict right drift oxygen exact dash ajar urge"
echo (Pick any account EXCEPT Account 1, which the backend is using)
echo.
pause
