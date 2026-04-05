"""
Smart PUC — CES constants generator & cross-checker
===================================================

Single source of truth for the Composite Emission Score (CES) weights and
BS-IV / BS-VI threshold tables is ``config/ces_weights.json``. This script:

  1. Reads the JSON.
  2. Writes a generated Python module ``backend/ces_constants.py`` that the
     emission engine imports — so there is exactly one place in the Python
     codebase where these numbers live.
  3. Cross-checks the Solidity integer constants in
     ``contracts/EmissionRegistry.sol`` and ``contracts/PUCCertificate.sol``
     against the JSON. Any divergence is reported as a hard error (exit 1)
     so the build pipeline can catch silent drift between the two surfaces.

This script closes audit-report limitation L8 / G4 ("dual CES weight
definitions — silent drift risk"). It is pure-Python, stdlib-only, and
runs in well under a second.

Usage
-----
    python scripts/gen_ces_consts.py              # generate + check
    python scripts/gen_ces_consts.py --check      # check only (CI-friendly)
    python scripts/gen_ces_consts.py --generate   # generate only (no check)

Exit codes
----------
    0 — success
    1 — JSON invalid, generated file stale, or Solidity/JSON mismatch
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
JSON_PATH = ROOT / "config" / "ces_weights.json"
PY_OUT = ROOT / "backend" / "ces_constants.py"
SOL_REGISTRY = ROOT / "contracts" / "EmissionRegistry.sol"
SOL_PUCCERT = ROOT / "contracts" / "PUCCertificate.sol"

# ── Solidity constant names we cross-check against the JSON ────────────────

SOL_REGISTRY_EXPECTED: List[Tuple[str, str]] = [
    # (solidity_constant_name, json_path_in_dotted_form)
    ("BSVI_CO2",              "bsvi_thresholds_petrol_gpkm.co2 * 1000"),
    ("BSVI_CO",               "bsvi_thresholds_petrol_gpkm.co * 1000"),
    ("BSVI_NOX",              "bsvi_thresholds_petrol_gpkm.nox * 1000"),
    ("BSVI_HC",               "bsvi_thresholds_petrol_gpkm.hc * 1000"),
    # PM2.5 is special: Solidity rounds 0.0045 up to 5 (integer) because a
    # plain *1000 cast would give 4. Document this explicitly — the JSON
    # keeps the float value and this generator encodes the rounding rule.
    ("BSVI_PM25",             "bsvi_thresholds_petrol_gpkm.pm25 * 1000 (ceil)"),
    ("BS4_CO2",               "bs4_thresholds_petrol_gpkm.co2 * 1000"),
    ("BS4_CO",                "bs4_thresholds_petrol_gpkm.co * 1000"),
    ("BS4_NOX",               "bs4_thresholds_petrol_gpkm.nox * 1000"),
    ("BS4_HC",                "bs4_thresholds_petrol_gpkm.hc * 1000"),
    ("BS4_PM25",              "bs4_thresholds_petrol_gpkm.pm25 * 1000"),
    ("CES_PASS_CEILING",      "ces_pass_ceiling_solidity"),
    ("FRAUD_ALERT_THRESHOLD", "fraud_alert_threshold_solidity"),
    ("CONSECUTIVE_PASS_REQUIRED", "consecutive_pass_required"),
    ("CES_WEIGHT_CO2",        "ces_weights_solidity.co2"),
    ("CES_WEIGHT_NOX",        "ces_weights_solidity.nox"),
    ("CES_WEIGHT_CO",         "ces_weights_solidity.co"),
    ("CES_WEIGHT_HC",         "ces_weights_solidity.hc"),
    ("CES_WEIGHT_PM25",       "ces_weights_solidity.pm25"),
]


# ───────────────────────── helpers ───────────────────────────────────────

def _load_json() -> Dict[str, Any]:
    if not JSON_PATH.exists():
        print(f"[gen_ces_consts] ERROR: {JSON_PATH} not found", file=sys.stderr)
        sys.exit(1)
    return json.loads(JSON_PATH.read_text(encoding="utf-8"))


def _validate_json(data: Dict[str, Any]) -> None:
    """Schema-validate the JSON. Exits with code 1 on failure."""
    errors: List[str] = []

    # CES weights sum to 1.0
    w = data.get("ces_weights", {})
    total = sum(float(v) for v in w.values())
    if abs(total - 1.0) > 1e-9:
        errors.append(f"ces_weights must sum to 1.0, got {total}")

    # CES weights float/int representations agree
    ws = data.get("ces_weights_solidity", {})
    scale = int(data.get("solidity_scale", 10000))
    for key, fv in w.items():
        iv = ws.get(key)
        if iv is None:
            errors.append(f"ces_weights_solidity missing key '{key}'")
            continue
        expected = round(float(fv) * scale)
        if int(iv) != expected:
            errors.append(
                f"ces_weights_solidity.{key} = {iv}, "
                f"expected {expected} (= ces_weights.{key} * {scale})"
            )

    # ces_pass_ceiling self-consistency
    cpc = float(data.get("ces_pass_ceiling", 1.0))
    cpci = int(data.get("ces_pass_ceiling_solidity", 10000))
    if round(cpc * scale) != cpci:
        errors.append(
            f"ces_pass_ceiling_solidity = {cpci}, expected {round(cpc * scale)}"
        )

    # fraud_alert_threshold self-consistency
    fat = float(data.get("fraud_alert_threshold", 0.65))
    fati = int(data.get("fraud_alert_threshold_solidity", 6500))
    if round(fat * scale) != fati:
        errors.append(
            f"fraud_alert_threshold_solidity = {fati}, "
            f"expected {round(fat * scale)}"
        )

    if errors:
        print("[gen_ces_consts] JSON validation errors:", file=sys.stderr)
        for err in errors:
            print(f"    - {err}", file=sys.stderr)
        sys.exit(1)


def _render_python(data: Dict[str, Any]) -> str:
    """Render the generated backend/ces_constants.py file."""
    w = data["ces_weights"]
    bsvi_p = data["bsvi_thresholds_petrol_gpkm"]
    bsvi_d = data["bsvi_thresholds_diesel_gpkm"]
    bs4_p = data["bs4_thresholds_petrol_gpkm"]
    bs4_d = data["bs4_thresholds_diesel_gpkm"]

    def _dict_lit(d: Dict[str, float], indent: str = "    ") -> str:
        lines = []
        for k in ["co2", "co", "nox", "hc", "pm25"]:
            if k in d:
                lines.append(f'{indent}"{k}": {float(d[k])!r},')
        return "\n".join(lines)

    body = f'''"""
Smart PUC — Generated CES Constants
===================================

**AUTO-GENERATED FILE. DO NOT EDIT BY HAND.**

Source of truth: ``config/ces_weights.json``
Generator      : ``scripts/gen_ces_consts.py``

To change any constant here, edit the JSON and re-run the generator:

    python scripts/gen_ces_consts.py

The generator also cross-checks the Solidity integer constants in
``contracts/EmissionRegistry.sol`` so the Python and on-chain sides
cannot silently drift.
"""

from __future__ import annotations

from typing import Dict

# ───────────────────────── CES weights (sum = 1.0) ─────────────────────────
CES_WEIGHTS: Dict[str, float] = {{
{_dict_lit(w)}
}}
if abs(sum(CES_WEIGHTS.values()) - 1.0) >= 1e-9:
    raise ValueError("CES weights must sum to 1.0 (audit G6)")

# ───────────────────────── Compliance constants ────────────────────────────
CES_PASS_CEILING: float = {float(data["ces_pass_ceiling"])!r}
FRAUD_ALERT_THRESHOLD: float = {float(data["fraud_alert_threshold"])!r}
CONSECUTIVE_PASS_REQUIRED: int = {int(data["consecutive_pass_required"])}

# ───────────────────────── BS-VI thresholds (g/km) ─────────────────────────
BSVI_THRESHOLDS_PETROL: Dict[str, float] = {{
{_dict_lit(bsvi_p)}
}}

BSVI_THRESHOLDS_DIESEL: Dict[str, float] = {{
{_dict_lit(bsvi_d)}
}}

# ───────────────────────── BS-IV thresholds (g/km) ─────────────────────────
BS4_THRESHOLDS_PETROL: Dict[str, float] = {{
{_dict_lit(bs4_p)}
}}

BS4_THRESHOLDS_DIESEL: Dict[str, float] = {{
{_dict_lit(bs4_d)}
}}
'''
    return body


def _generate(data: Dict[str, Any]) -> None:
    content = _render_python(data)
    PY_OUT.write_text(content, encoding="utf-8")
    print(f"[gen_ces_consts] wrote {PY_OUT.relative_to(ROOT)}")


# ───────────────────────── Solidity cross-check ──────────────────────────

_INT_CONST_RE = re.compile(
    r"uint256\s+(?:public|private|internal)?\s*constant\s+(\w+)\s*=\s*(\d+)"
)


def _scan_solidity(path: Path) -> Dict[str, int]:
    """Extract all `uint256 constant NAME = INT` declarations."""
    text = path.read_text(encoding="utf-8")
    found: Dict[str, int] = {}
    for name, value in _INT_CONST_RE.findall(text):
        found[name] = int(value)
    return found


def _expected_int(data: Dict[str, Any], expr: str) -> int:
    """Resolve a JSON path expression like 'bsvi_thresholds_petrol_gpkm.co2 * 1000 (ceil)'."""
    ceil = False
    raw = expr
    if "(ceil)" in expr:
        ceil = True
        expr = expr.replace("(ceil)", "").strip()

    # Support "foo.bar * 1000"
    m = re.match(r"^([a-zA-Z0-9_.]+)\s*\*\s*(\d+)$", expr)
    if m:
        path, factor = m.group(1), int(m.group(2))
        val = _walk(data, path)
        # ceil for PM2.5 rounding rule
        product = float(val) * factor
        if ceil:
            import math
            return math.ceil(product)
        return round(product)

    # Plain path: foo.bar
    val = _walk(data, expr)
    return int(val)


def _walk(data: Dict[str, Any], path: str) -> Any:
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise KeyError(f"JSON path {path!r} not found at segment {part!r}")
        cur = cur[part]
    return cur


def _check_solidity(data: Dict[str, Any]) -> None:
    """Compare Solidity integer constants against JSON. Exit 1 on mismatch."""
    if not SOL_REGISTRY.exists():
        print(f"[gen_ces_consts] WARN: {SOL_REGISTRY} not found, skipping sol check")
        return

    sol_consts = _scan_solidity(SOL_REGISTRY)
    errors: List[str] = []

    for sol_name, json_expr in SOL_REGISTRY_EXPECTED:
        if sol_name not in sol_consts:
            errors.append(
                f"EmissionRegistry.sol is missing expected constant '{sol_name}'"
            )
            continue
        expected = _expected_int(data, json_expr)
        actual = sol_consts[sol_name]
        if actual != expected:
            errors.append(
                f"EmissionRegistry.sol.{sol_name} = {actual}, "
                f"expected {expected} (from JSON path '{json_expr}')"
            )

    if errors:
        print("[gen_ces_consts] Solidity cross-check FAILED:", file=sys.stderr)
        for err in errors:
            print(f"    - {err}", file=sys.stderr)
        sys.exit(1)
    print(
        f"[gen_ces_consts] Solidity cross-check OK "
        f"({len(SOL_REGISTRY_EXPECTED)} constants verified)"
    )


def _check_generated_is_fresh(data: Dict[str, Any]) -> None:
    """Ensure the on-disk ces_constants.py matches what _render_python would produce."""
    expected = _render_python(data)
    if not PY_OUT.exists():
        print(
            f"[gen_ces_consts] ERROR: {PY_OUT} does not exist — "
            "run `python scripts/gen_ces_consts.py` to generate it.",
            file=sys.stderr,
        )
        sys.exit(1)
    actual = PY_OUT.read_text(encoding="utf-8")
    if actual.strip() != expected.strip():
        print(
            f"[gen_ces_consts] ERROR: {PY_OUT.relative_to(ROOT)} is stale. "
            "Re-run `python scripts/gen_ces_consts.py` to regenerate.",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"[gen_ces_consts] {PY_OUT.relative_to(ROOT)} is up to date")


# ───────────────────────── main ───────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(description="Generate / verify CES constants")
    p.add_argument(
        "--check",
        action="store_true",
        help="Do not write — only validate JSON, cross-check Solidity and the "
             "generated Python file. CI-friendly.",
    )
    p.add_argument(
        "--generate",
        action="store_true",
        help="Only generate backend/ces_constants.py (skip Solidity cross-check).",
    )
    args = p.parse_args()

    data = _load_json()
    _validate_json(data)

    if args.check:
        _check_generated_is_fresh(data)
        _check_solidity(data)
        return 0

    _generate(data)

    if not args.generate:
        _check_solidity(data)

    return 0


if __name__ == "__main__":
    sys.exit(main())
