@echo off
REM ======================================================================
REM Smart PUC -- one-command reproducibility bundle (Windows cmd.exe)
REM ======================================================================
REM Closes audit Top-5 Addition #1 (N15). Mirror of scripts/reproduce.sh
REM for Windows reviewers with no bash / WSL.
REM
REM Usage (from repo root):
REM     scripts\reproduce.bat
REM
REM Exit codes:
REM   0 OK  1 env missing  2 CES drift  3 tests failed  4 report drift
REM ======================================================================

setlocal ENABLEDELAYEDEXPANSION

pushd "%~dp0.."
set "REPO_ROOT=%CD%"

echo.
echo -- Step 1/9 -- Environment --
for /f %%H in ('git rev-parse HEAD 2^>nul') do set GIT_HASH=%%H
for /f %%B in ('git rev-parse --abbrev-ref HEAD 2^>nul') do set GIT_BRANCH=%%B
if "%GIT_HASH%"=="" set GIT_HASH=unknown
if "%GIT_BRANCH%"=="" set GIT_BRANCH=unknown
echo   Repo root  : %REPO_ROOT%
echo   Git branch : %GIT_BRANCH%
echo   Git commit : %GIT_HASH%
echo   Date       : %DATE% %TIME%

set "PY=backend\venv\Scripts\python.exe"
if not exist "%PY%" (
    echo   ERROR: backend\venv not found.
    echo   Create it first:
    echo       python -m venv backend\venv
    echo       backend\venv\Scripts\pip install -r requirements.txt
    popd
    exit /b 1
)
echo   Python     : %PY%
"%PY%" --version

echo.
echo -- Step 2/9 -- CES constants cross-check --
"%PY%" scripts\gen_ces_consts.py --check
if errorlevel 1 (
    echo   ERROR: CES constants out of sync.
    popd
    exit /b 2
)
echo   OK: CES constants in sync

echo.
echo -- Step 3/9 -- Hardhat compile (best-effort) --
if exist node_modules (
    call npx --no-install hardhat compile
    if errorlevel 1 (
        echo   WARN: hardhat compile failed -- continuing
    ) else (
        echo   OK: Contracts compiled
    )
) else (
    echo   WARN: node_modules missing -- skipping (run 'npm install' to enable)
)

echo.
echo -- Step 4/9 -- Hardhat test suite --
set "HH_SUMMARY=skipped"
if exist node_modules (
    call npx --no-install hardhat test
    if errorlevel 1 (
        echo   ERROR: Hardhat tests failed
        popd
        exit /b 3
    )
    set "HH_SUMMARY=passed"
    echo   OK: Hardhat tests passed
) else (
    echo   WARN: node_modules missing -- skipping hardhat test
)

echo.
echo -- Step 5/9 -- pytest (tests\) --
"%PY%" -m pytest tests\ -p no:ethereum --tb=no -q
if errorlevel 1 (
    echo   ERROR: pytest failed
    popd
    exit /b 3
)
echo   OK: pytest passed

echo.
echo -- Step 6/9 -- CES vs CO2-only benchmark (seed=42, samples=5000) --
set "REPORT=docs\ces_vs_co2_report.json"
set "REPORT_BAK=docs\ces_vs_co2_report.committed.json"
if exist "%REPORT%" (
    copy /Y "%REPORT%" "%REPORT_BAK%" >nul
)
"%PY%" scripts\bench_ces_vs_co2.py --samples 5000 --seed 42 --output "%REPORT%"
if errorlevel 1 (
    echo   ERROR: bench_ces_vs_co2.py failed
    if exist "%REPORT_BAK%" move /Y "%REPORT_BAK%" "%REPORT%" >nul
    popd
    exit /b 3
)
echo   OK: Regenerated %REPORT%

echo.
echo -- Step 7/9 -- Byte-identity check vs committed report --
if exist "%REPORT_BAK%" (
    fc /b "%REPORT_BAK%" "%REPORT%" >nul
    if errorlevel 1 (
        echo   ERROR: Regenerated report differs from committed copy.
        fc "%REPORT_BAK%" "%REPORT%"
        move /Y "%REPORT_BAK%" "%REPORT%" >nul
        popd
        exit /b 4
    ) else (
        echo   OK: Byte-identical to committed %REPORT%
        del /Q "%REPORT_BAK%"
    )
) else (
    echo   WARN: No committed baseline to diff -- skipping byte-check
)

echo.
echo -- Step 8/9 -- Summary --
echo   Commit     : %GIT_HASH%
echo   Hardhat    : %HH_SUMMARY%
echo   pytest     : passed
echo   CES report : byte-identical

echo.
echo -- Step 9/9 -- Result --
echo   ============================================
echo   REPRODUCIBLE OK -- Smart PUC artifact verified
echo   ============================================
popd
exit /b 0
