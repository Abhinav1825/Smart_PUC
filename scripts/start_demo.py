#!/usr/bin/env python3
"""
SmartPUC Demo Launcher
======================
Starts the full demo stack with a single command:
  Hardhat node -> deploy contracts -> seed DB -> backend -> frontend

Usage:
    python scripts/start_demo.py

Press Ctrl+C to shut everything down cleanly.
"""

import os
import sys
import time
import shutil
import subprocess
import webbrowser

# ---------------------------------------------------------------------------
# Resolve project root (one level up from this script)
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, ".."))

# All subprocesses we spawn, so we can clean them up on exit.
_processes: list[subprocess.Popen] = []


def banner():
    print()
    print("=" * 50)
    print("       SmartPUC Demo Launcher")
    print("=" * 50)
    print()


def check_prerequisites():
    """Verify that required tools are available on PATH."""
    print("[1/7] Checking prerequisites...")

    # Python (we are already running, but print version)
    print(f"  Python  : {sys.version.split()[0]} ({sys.executable})")

    # Node.js
    node = shutil.which("node")
    if node is None:
        sys.exit("  ERROR: Node.js not found on PATH. Install it from https://nodejs.org")
    result = subprocess.run([node, "--version"], capture_output=True, text=True)
    print(f"  Node.js : {result.stdout.strip()}")

    # npm / npx
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if npx is None:
        sys.exit("  ERROR: npx not found on PATH. It ships with Node.js >= 8.")
    print(f"  npx     : {npx}")

    # Check node_modules exists (npm install done)
    node_modules = os.path.join(PROJECT_ROOT, "node_modules")
    if not os.path.isdir(node_modules):
        sys.exit(
            "  ERROR: node_modules/ not found. Run `npm install` in the project root first."
        )
    print("  npm pkgs: node_modules/ found")
    print()


def _wait_for_rpc(url="http://127.0.0.1:8545", timeout=30):
    """Poll the RPC endpoint until it responds or timeout."""
    import urllib.request
    import urllib.error
    import json

    body = json.dumps({"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1}).encode()
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            resp = urllib.request.urlopen(req, timeout=2)
            if resp.status == 200:
                return True
        except (urllib.error.URLError, ConnectionRefusedError, OSError):
            pass
        time.sleep(0.5)
    return False


def start_hardhat_node():
    """Start `npx hardhat node` as a background process."""
    print("[2/7] Starting Hardhat local node...")
    # Redirect stdout/stderr to a log file to prevent pipe buffer
    # deadlocks on Windows while still keeping the output for debugging.
    log_path = os.path.join(PROJECT_ROOT, "hardhat_node.log")
    log_file = open(log_path, "w")
    proc = subprocess.Popen(
        "npx hardhat node",
        cwd=PROJECT_ROOT,
        stdout=log_file,
        stderr=log_file,
        shell=True,
    )
    _processes.append(proc)
    # Keep the file handle so we can close it during cleanup
    proc._log_file = log_file  # type: ignore[attr-defined]
    print(f"  PID {proc.pid} — waiting for RPC to respond (up to 60s)...")
    print(f"  (log: {log_path})")

    if not _wait_for_rpc(timeout=60):
        if proc.poll() is not None:
            sys.exit(f"  ERROR: Hardhat node exited early. Check {log_path}")
        sys.exit("  ERROR: Hardhat node did not respond within 60 seconds.")

    # Extra stabilization — let the node finish printing its account
    # table and fully settle before we start sending deploy transactions.
    time.sleep(3)
    print("  Hardhat node is running on http://127.0.0.1:8545")
    print()


def deploy_contracts():
    """Compile (if needed) then deploy contracts, streaming output."""
    # ── Pre-compile so deploy.js doesn't trigger a slow viaIR compile ──
    artifacts_dir = os.path.join(PROJECT_ROOT, "artifacts", "contracts")
    if not os.path.isdir(artifacts_dir):
        print("[3/7] Compiling contracts (first time — may take 2-4 minutes)...")
        compile_proc = subprocess.Popen(
            "npx hardhat compile",
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            shell=True,
            text=True,
        )
        for line in compile_proc.stdout:
            print(f"  {line}", end="")
        compile_proc.wait()
        if compile_proc.returncode != 0:
            sys.exit("  ERROR: Compilation failed.")
        print()

    print("[3/7] Deploying 3 UUPS proxy contracts (~45 seconds — do NOT press Ctrl+C)...")
    # Set env to disable undici headersTimeout (set to 0 = no limit).
    # The UUPS proxy deployment for PUCCertificate can take >20s on slower
    # machines, causing the default 20s undici timeout to fire.
    deploy_env = {**os.environ, "DO_NOT_SET_THIS_ENV_VAR____IS_HARDHAT_CI": "true"}
    proc = subprocess.Popen(
        "npx hardhat run scripts/deploy.js --network localhost --no-compile",
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        shell=True,
        text=True,
        env=deploy_env,
    )
    # Stream output line-by-line so the user sees progress
    for line in proc.stdout:
        print(f"  {line}", end="")
    proc.wait()
    if proc.returncode != 0:
        sys.exit(f"  ERROR: Contract deployment failed (exit code {proc.returncode}).")
    print()


def seed_database():
    """Run gen_demo_cache.py to seed demo data."""
    print("[4/7] Seeding demo data...")
    proc = subprocess.Popen(
        [sys.executable, os.path.join(SCRIPT_DIR, "gen_demo_cache.py")],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    for line in proc.stdout:
        print(f"  {line}", end="")
    proc.wait()
    if proc.returncode != 0:
        print("  WARNING: Demo cache seeding failed — continuing anyway.")
    print()


def start_backend():
    """Start the FastAPI backend via uvicorn."""
    print("[5/7] Starting backend (uvicorn)...")
    # Redirect to log file — NOT subprocess.PIPE, which deadlocks on
    # Windows when the buffer fills and nobody drains it.
    log_path = os.path.join(PROJECT_ROOT, "backend.log")
    log_file = open(log_path, "w")
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "backend.main:app",
            "--host", "0.0.0.0",
            "--port", "5000",
        ],
        cwd=PROJECT_ROOT,
        stdout=log_file,
        stderr=log_file,
    )
    _processes.append(proc)
    proc._log_file = log_file  # type: ignore[attr-defined]
    print(f"  PID {proc.pid} — waiting for backend to start...")
    print(f"  (log: {log_path})")

    # Wait for the backend HTTP endpoint to respond
    if not _wait_for_http("http://127.0.0.1:5000/api/status", timeout=20):
        if proc.poll() is not None:
            log_file.flush()
            with open(log_path, "r") as f:
                print(f.read())
            sys.exit("  ERROR: Backend exited early. Check backend.log")
        print("  WARNING: Backend did not respond to health check, but process is alive.")

    print("  Backend is running on http://localhost:5000")
    print()


def _wait_for_http(url, timeout=15):
    """Poll an HTTP GET endpoint until it responds 200."""
    import urllib.request
    import urllib.error

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = urllib.request.urlopen(url, timeout=2)
            if resp.status == 200:
                return True
        except (urllib.error.URLError, ConnectionRefusedError, OSError):
            pass
        time.sleep(0.5)
    return False


def start_frontend():
    """Start a static file server for the frontend."""
    print("[6/7] Starting frontend (http-server)...")
    log_path = os.path.join(PROJECT_ROOT, "frontend.log")
    log_file = open(log_path, "w")
    proc = subprocess.Popen(
        "npx http-server frontend -p 3000 -c-1 --cors",
        cwd=PROJECT_ROOT,
        stdout=log_file,
        stderr=log_file,
        shell=True,
    )
    _processes.append(proc)
    proc._log_file = log_file  # type: ignore[attr-defined]
    time.sleep(2)
    print(f"  PID {proc.pid}")
    print("  Frontend is running on http://localhost:3000")
    print()


def cleanup():
    """Terminate all background subprocesses."""
    print("\nShutting down...")
    for proc in reversed(_processes):
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
                print(f"  Terminated PID {proc.pid}")
            except subprocess.TimeoutExpired:
                proc.kill()
                print(f"  Killed PID {proc.pid}")
            except Exception:
                proc.kill()
                print(f"  Killed PID {proc.pid}")
        # Close any log file handles
        log_file = getattr(proc, "_log_file", None)
        if log_file:
            try:
                log_file.close()
            except Exception:
                pass
    print("All processes stopped. Goodbye.")


def main():
    banner()
    check_prerequisites()

    try:
        start_hardhat_node()
        deploy_contracts()
        seed_database()
        start_backend()
        start_frontend()

        print("=" * 50)
        print("  \u2713 Demo ready!")
        print("=" * 50)
        print()
        print("  Backend  : http://localhost:5000")
        print("  Frontend : http://localhost:3000")
        print("  API docs : http://localhost:5000/docs")
        print()

        # Try to open the frontend in the default browser
        try:
            webbrowser.open("http://localhost:3000")
            print("  (opened http://localhost:3000 in your browser)")
        except Exception:
            print("  (could not auto-open browser — navigate manually)")

        print()
        print("Press Ctrl+C to stop all services.")
        print()

        # Block until Ctrl+C
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        pass
    except SystemExit as e:
        print(str(e))
    finally:
        cleanup()


if __name__ == "__main__":
    main()
