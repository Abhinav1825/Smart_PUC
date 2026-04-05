"""
Smart PUC — Single-terminal orchestrator
========================================

Starts the entire 3-node stack from one command and streams every
service's stdout into the current terminal with a colored prefix so
you can read backend + chain + frontend logs side by side. Hit Ctrl+C
once and everything shuts down cleanly.

Services launched (in order):
  1. Hardhat node        :8545  (chainId 31337, unlocked dev accounts)
  2. deploy.js           one-shot, writes docs/DEPLOYED_ADDRESSES.json
  3. FastAPI backend     :5000  (uvicorn)
  4. Static frontend     :3000  (Python http.server)
  5. (optional) OBD node — add --with-obd to stream signed telemetry

Auto-behaviours
---------------
- If the Hardhat node is already running on :8545 the script re-uses it
  instead of spawning a second one.
- After the node is up, the orchestrator reads `eth_accounts` from the
  RPC and patches `.env` so that `STATION_ADDRESS` and
  `STATION_DEVICE_ADDRESS` always match the signer[1]/signer[2] of the
  *live* chain, regardless of which mnemonic the fresh node uses.
- After deploy, it reads the fresh `REGISTRY_ADDRESS` from
  `build/contracts/EmissionRegistry.json` and patches `.env` again.
- Finally it opens http://127.0.0.1:3000/index.html in the default
  browser so you can start clicking immediately.

Usage
-----
    python scripts/run_all.py              # chain + deploy + backend + frontend
    python scripts/run_all.py --with-obd   # also launch OBD device simulator
    python scripts/run_all.py --no-browser # skip auto-opening the dashboard
    python scripts/run_all.py --skip-deploy  # re-use existing deployment
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
ARTIFACTS = ROOT / "build" / "contracts"
DEPLOYED_ADDRESSES = ROOT / "docs" / "DEPLOYED_ADDRESSES.json"

RPC_URL = "http://127.0.0.1:8545"
CHAIN_ID = "31337"
BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 5000
FRONTEND_PORT = 3000


# ─────────────────────────── Coloured logging ───────────────────────────

class Colour:
    RESET = "\x1b[0m"
    BOLD = "\x1b[1m"
    RED = "\x1b[31m"
    GREEN = "\x1b[32m"
    YELLOW = "\x1b[33m"
    BLUE = "\x1b[34m"
    MAGENTA = "\x1b[35m"
    CYAN = "\x1b[36m"
    WHITE = "\x1b[37m"
    GREY = "\x1b[90m"


SERVICE_COLORS = {
    "orch":     Colour.BOLD + Colour.WHITE,
    "chain":    Colour.CYAN,
    "deploy":   Colour.MAGENTA,
    "backend":  Colour.GREEN,
    "frontend": Colour.YELLOW,
    "obd":      Colour.BLUE,
}


def _enable_ansi_on_windows() -> None:
    """Enable ANSI escape processing in Windows 10+ cmd / PowerShell."""
    if os.name != "nt":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        hOut = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_ulong()
        if kernel32.GetConsoleMode(hOut, ctypes.byref(mode)):
            # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
            kernel32.SetConsoleMode(hOut, mode.value | 0x0004)
    except Exception:
        pass


def _force_utf8_stdout() -> None:
    """Force sys.stdout/stderr to utf-8 with errors='replace' so that
    child processes whose output contains non-cp1252 characters (em-dash,
    box-drawing, etc.) do not crash our log pumps on Windows."""
    for attr in ("stdout", "stderr"):
        stream = getattr(sys, attr, None)
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def log(service: str, message: str, *, stream=sys.stdout) -> None:
    colour = SERVICE_COLORS.get(service, Colour.WHITE)
    tag = f"{colour}[{service:8s}]{Colour.RESET}"
    for line in str(message).rstrip().splitlines() or [""]:
        out = f"{tag} {line}\n"
        try:
            stream.write(out)
        except UnicodeEncodeError:
            # Fallback for old Pythons that couldn't reconfigure stdout
            stream.write(out.encode("ascii", "replace").decode("ascii"))
    try:
        stream.flush()
    except Exception:
        pass


# ─────────────────────────── .env helpers ───────────────────────────────

def _read_env() -> Dict[str, str]:
    result: Dict[str, str] = {}
    if not ENV_FILE.exists():
        return result
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        result[key.strip()] = value.strip()
    return result


def _patch_env(updates: Dict[str, str]) -> None:
    """Rewrite .env in place, updating or appending the given keys."""
    if not ENV_FILE.exists():
        lines: List[str] = []
    else:
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()

    seen: set = set()
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in updates:
            lines[i] = f"{key}={updates[key]}"
            seen.add(key)

    # Append any keys we did not find in the file.
    missing = [k for k in updates if k not in seen]
    if missing:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("# ─── Auto-patched by scripts/run_all.py ───")
        for key in missing:
            lines.append(f"{key}={updates[key]}")

    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ─────────────────────────── JSON-RPC helpers ───────────────────────────

def _rpc_call(method: str, params: list, timeout: float = 2.0):
    req = urllib.request.Request(
        RPC_URL,
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read()).get("result")


def _wait_for_rpc(timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            block = _rpc_call("eth_blockNumber", [])
            if block is not None:
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def _wait_for_http(url: str, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                if 200 <= resp.status < 500:
                    return True
        except (urllib.error.URLError, urllib.error.HTTPError, ConnectionError, OSError):
            pass
        time.sleep(0.3)
    return False


# ─────────────────────────── Process management ─────────────────────────

def _create_windows_job_object():
    """Create a Windows Job Object with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE.

    Any child process we assign to this job will be terminated by the
    kernel automatically when the orchestrator exits — regardless of
    whether the exit was clean (Ctrl+C), an uncaught exception, or a
    forced taskkill. This is the single most reliable cleanup mechanism
    on Windows for orphaned subprocesses.
    """
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None

        # JOBOBJECT_EXTENDED_LIMIT_INFORMATION with KILL_ON_JOB_CLOSE.
        JobObjectExtendedLimitInformation = 9
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            job, JobObjectExtendedLimitInformation,
            ctypes.byref(info), ctypes.sizeof(info),
        ):
            return None
        return job
    except Exception:
        return None


def _assign_to_job(job, proc: subprocess.Popen) -> None:
    if job is None or os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        PROCESS_ALL_ACCESS = 0x1F0FFF
        handle = kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, proc.pid)
        if handle:
            kernel32.AssignProcessToJobObject(job, handle)
            kernel32.CloseHandle(handle)
    except Exception:
        pass


class Runner:
    def __init__(self) -> None:
        self.procs: List[subprocess.Popen] = []
        self.threads: List[threading.Thread] = []
        self.shutting_down = False
        self._job = _create_windows_job_object()
        if self._job is not None:
            log("orch", "Windows Job Object enabled — children will auto-die on exit")

    def spawn(self, service: str, cmd: List[str], *, env: Optional[dict] = None,
              cwd: Optional[Path] = None) -> subprocess.Popen:
        log("orch", f"launch {service}: {' '.join(cmd)}")
        creation_flags = 0
        if os.name == "nt":
            creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd or ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env={**os.environ, **(env or {})},
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
            creationflags=creation_flags,
        )
        _assign_to_job(self._job, proc)
        self.procs.append(proc)
        t = threading.Thread(target=self._pump, args=(service, proc), daemon=True)
        t.start()
        self.threads.append(t)
        return proc

    def _pump(self, service: str, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            if self.shutting_down:
                return
            log(service, line.rstrip())
        rc = proc.wait()
        if not self.shutting_down:
            log("orch", f"{service} exited with code {rc}")

    def run_blocking(self, service: str, cmd: List[str], *, env: Optional[dict] = None,
                     cwd: Optional[Path] = None) -> int:
        log("orch", f"run    {service}: {' '.join(cmd)}")
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd or ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env={**os.environ, **(env or {})},
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            log(service, line.rstrip())
        return proc.wait()

    def shutdown(self) -> None:
        if self.shutting_down:
            return
        self.shutting_down = True
        log("orch", "shutting down all services...")

        # Phase 1 — polite signal.
        for proc in reversed(self.procs):
            if proc.poll() is not None:
                continue
            try:
                if os.name == "nt":
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    proc.terminate()
            except Exception:
                pass

        # Phase 2 — taskkill /T /F on Windows to wipe the whole tree.
        # npx and ganache wrap the real node.exe in a cmd shim which
        # CTRL_BREAK alone does not always reach.
        if os.name == "nt":
            for proc in self.procs:
                if proc.poll() is not None:
                    continue
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                except Exception:
                    pass

        deadline = time.time() + 5.0
        for proc in self.procs:
            remaining = max(0.0, deadline - time.time())
            try:
                proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except Exception:
                    pass
        log("orch", "bye.")


# ─────────────────────────── Steps ──────────────────────────────────────

def ensure_node(runner: Runner, hardhat_node_cmd: List[str]) -> None:
    if _wait_for_rpc(timeout=0.5):
        log("orch", f"hardhat node already listening at {RPC_URL} — reusing it")
        return
    runner.spawn("chain", hardhat_node_cmd)
    if not _wait_for_rpc(timeout=30.0):
        raise RuntimeError(f"Hardhat node failed to come up at {RPC_URL} within 30s")
    log("orch", f"hardhat node is live at {RPC_URL}")


def sync_env_from_chain() -> Dict[str, str]:
    """Ask the live RPC for its accounts and patch STATION_ADDRESS /
    STATION_DEVICE_ADDRESS into .env so they always match signer[1] and
    signer[2] of whichever mnemonic the node is currently running."""
    accounts = _rpc_call("eth_accounts", []) or []
    if len(accounts) < 3:
        raise RuntimeError(f"RPC returned too few accounts: {accounts}")
    station = _to_checksum(accounts[1])
    device = _to_checksum(accounts[2])
    updates = {
        "RPC_URL": RPC_URL,
        "CHAIN_ID": CHAIN_ID,
        "STATION_ADDRESS": station,
        "STATION_DEVICE_ADDRESS": device,
        "PRIVATE_KEY": "",
        "OBD_DEVICE_PRIVATE_KEY": "",
        "STATION_DEVICE_PRIVATE_KEY": "",
    }
    _patch_env(updates)
    log("orch", f"patched .env  STATION_ADDRESS={station}  STATION_DEVICE_ADDRESS={device}")
    return {"station": station, "device": device}


def _to_checksum(address: str) -> str:
    """Minimal EIP-55 checksum — avoids pulling in eth_utils here."""
    try:
        from web3 import Web3  # type: ignore
        return Web3.to_checksum_address(address)
    except Exception:
        # fallback: best-effort, keeps the lowercase address
        return address


def deploy_contracts(runner: Runner) -> str:
    env = {"RPC_URL": RPC_URL, "CHAIN_ID": CHAIN_ID}
    npx = "npx.cmd" if os.name == "nt" else "npx"
    rc = runner.run_blocking(
        "deploy",
        [npx, "hardhat", "run", "scripts/deploy.js", "--network", "localhost"],
        env=env,
    )
    if rc != 0:
        raise RuntimeError(f"deploy.js exited with code {rc}")
    registry = _read_registry_address()
    if registry:
        _patch_env({"REGISTRY_ADDRESS": registry})
        log("orch", f"patched .env  REGISTRY_ADDRESS={registry}")
    return registry


def _read_registry_address() -> str:
    artifact = ARTIFACTS / "EmissionRegistry.json"
    if not artifact.exists():
        return ""
    data = json.loads(artifact.read_text(encoding="utf-8"))
    networks = data.get("networks", {})
    if CHAIN_ID in networks:
        return networks[CHAIN_ID].get("address", "")
    # fall back to the last entry
    for entry in reversed(list(networks.values())):
        if entry.get("address"):
            return entry["address"]
    return ""


def start_backend(runner: Runner) -> None:
    cmd = [
        sys.executable, "-u", "-m", "uvicorn", "backend.main:app",
        "--host", "0.0.0.0", "--port", str(BACKEND_PORT), "--log-level", "info",
    ]
    runner.spawn("backend", cmd)
    if not _wait_for_http(f"http://{BACKEND_HOST}:{BACKEND_PORT}/health", timeout=30.0):
        raise RuntimeError("FastAPI backend failed to start within 30s")
    log("orch", f"backend is live at http://{BACKEND_HOST}:{BACKEND_PORT}  (swagger: /docs)")


def start_frontend(runner: Runner) -> None:
    cmd = [sys.executable, "-u", "-m", "http.server", str(FRONTEND_PORT), "--bind", "127.0.0.1"]
    runner.spawn("frontend", cmd, cwd=ROOT / "frontend")
    if not _wait_for_http(f"http://127.0.0.1:{FRONTEND_PORT}/index.html", timeout=10.0):
        log("orch", "frontend HTTP probe timed out (but the server may still be coming up)")
    else:
        log("orch", f"frontend is live at http://127.0.0.1:{FRONTEND_PORT}/index.html")


def start_obd(runner: Runner) -> None:
    cmd = [sys.executable, "-u", "-m", "obd_node.obd_device",
           "--count", "50", "--interval", "3"]
    runner.spawn("obd", cmd)


# ─────────────────────────── Entry point ────────────────────────────────

def main() -> int:
    _force_utf8_stdout()
    _enable_ansi_on_windows()

    parser = argparse.ArgumentParser(description="Smart PUC single-terminal launcher")
    parser.add_argument("--with-obd", action="store_true",
                        help="Also start the OBD device simulator (Node 1)")
    parser.add_argument("--no-browser", action="store_true",
                        help="Do not auto-open the dashboard in the browser")
    parser.add_argument("--skip-deploy", action="store_true",
                        help="Re-use the existing deployment in build/contracts/*.json")
    args = parser.parse_args()

    print()
    log("orch", "Smart PUC — single-terminal launcher")
    log("orch", f"repo root: {ROOT}")

    runner = Runner()

    def _handle_sigint(signum, frame):
        runner.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, _handle_sigint)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_sigint)

    try:
        # 1. Chain
        npx = "npx.cmd" if os.name == "nt" else "npx"
        ensure_node(runner, [npx, "hardhat", "node"])

        # 2. Sync .env with the live accounts
        sync_env_from_chain()

        # 3. Deploy (or skip)
        if args.skip_deploy and _read_registry_address():
            log("orch", f"--skip-deploy set, re-using registry {_read_registry_address()}")
        else:
            deploy_contracts(runner)

        # 4. Backend
        start_backend(runner)

        # 5. Frontend
        start_frontend(runner)

        # 6. Optional OBD simulator
        if args.with_obd:
            start_obd(runner)

        print()
        log("orch", "=" * 60)
        log("orch", "ALL SERVICES RUNNING")
        log("orch", "=" * 60)
        log("orch", f"  Chain (Hardhat) : {RPC_URL}  (chainId {CHAIN_ID})")
        log("orch", f"  Backend (API)   : http://{BACKEND_HOST}:{BACKEND_PORT}")
        log("orch", f"  Swagger UI      : http://{BACKEND_HOST}:{BACKEND_PORT}/docs")
        log("orch", f"  Frontend        : http://127.0.0.1:{FRONTEND_PORT}/index.html")
        log("orch", "")
        env_now = _read_env()
        log("orch", f"  Login as        : {env_now.get('AUTH_USERNAME','admin')} / {env_now.get('AUTH_PASSWORD','')}")
        log("orch", f"  X-API-Key       : {env_now.get('API_KEY','')}")
        log("orch", "")
        log("orch", "  Press Ctrl+C to stop everything cleanly.")
        log("orch", "=" * 60)
        print()

        if not args.no_browser:
            try:
                webbrowser.open(f"http://127.0.0.1:{FRONTEND_PORT}/index.html")
            except Exception:
                pass

        # Block forever, streaming service logs. If any critical service
        # crashes we abort the whole stack so the user notices.
        while True:
            time.sleep(1.0)
            for proc in runner.procs:
                if proc.poll() is not None and not runner.shutting_down:
                    log("orch", f"service exited unexpectedly (code {proc.returncode}) — shutting down")
                    runner.shutdown()
                    return 1
    except KeyboardInterrupt:
        runner.shutdown()
        return 0
    except Exception as exc:
        log("orch", f"FATAL: {exc}", stream=sys.stderr)
        runner.shutdown()
        return 1


if __name__ == "__main__":
    sys.exit(main())
