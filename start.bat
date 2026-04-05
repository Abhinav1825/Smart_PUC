@echo off
REM Smart PUC — one-line launcher. Starts chain + deploy + backend + frontend
REM in a single terminal. Pass --with-obd to also stream the OBD simulator.
REM Pass --skip-deploy to re-use an existing deployment. Pass --no-browser
REM to skip auto-opening the dashboard.

cd /d "%~dp0"
python -u scripts\run_all.py %*
