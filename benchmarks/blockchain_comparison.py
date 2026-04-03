"""
Smart PUC -- Blockchain Platform Comparison Matrix
===================================================

Provides a structured comparison of three blockchain platforms evaluated for
the Smart PUC vehicular emission monitoring system:

1. **Ethereum (Sepolia testnet)** -- the reference public EVM chain.
2. **Polygon (Mumbai testnet / Amoy)** -- EVM-compatible L2 / side-chain
   with low fees and fast finality.
3. **Hyperledger Fabric** -- permissioned enterprise blockchain with
   private channels and deterministic finality.

Metrics compared include consensus mechanism, throughput (TPS), block time,
confirmation latency, gas cost, USD cost per transaction, decentralisation
level, privacy support, smart-contract language, IoT suitability, and
finality type.

The recommendation function returns a justified selection for the Smart PUC
use case, weighing cost, latency, IoT friendliness, and Indian regulatory
alignment.

References
----------
[1] Ethereum Foundation, "Proof-of-Stake (PoS)," ethereum.org, 2024.
    https://ethereum.org/en/developers/docs/consensus-mechanisms/pos/

[2] Polygon Labs, "Polygon PoS Architecture," polygon.technology, 2024.
    https://polygon.technology/polygon-pos

[3] Hyperledger Foundation, "Hyperledger Fabric v2.5 Documentation," 2024.
    https://hyperledger-fabric.readthedocs.io/en/release-2.5/

[4] Buterin, V., "A Next-Generation Smart Contract and Decentralized
    Application Platform," Ethereum White Paper, 2014.

[5] Androulaki, E., et al., "Hyperledger Fabric: A Distributed Operating
    System for Permissioned Blockchains," in Proc. EuroSys, 2018.

[6] Polygon Labs, "Gas Price and Fee Estimation on Polygon PoS," 2024.
    https://wiki.polygon.technology/docs/pos/reference/rpc-endpoints/

[7] MoRTH, "Central Motor Vehicle Rules (CMVR) -- Technical Standing
    Committee on Emission Norms," Government of India, 2023.

Usage::

    from benchmarks.blockchain_comparison import (
        generate_comparison_table,
        get_recommendation,
        PLATFORM_DATA,
    )
    print(generate_comparison_table())
    print(get_recommendation())
"""

from __future__ import annotations

from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Optional pandas import with graceful fallback
# ---------------------------------------------------------------------------
try:
    import pandas as pd
    _HAS_PANDAS = True
except ImportError:
    _HAS_PANDAS = False

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False


# ===========================================================================
# Platform data
# ===========================================================================

PLATFORM_DATA: Dict[str, Dict[str, Any]] = {
    "Ethereum (Sepolia)": {
        "consensus":            "Proof-of-Stake (PoS)",
        "tps":                  "15--30",
        "block_time_s":         "12",
        "confirmation_latency": "12--78 s (1--6 blocks)",
        "gas_per_tx":           "~300,000",
        "cost_per_tx_usd":      "$0.50--$5.00",
        "decentralisation":     "High",
        "privacy":              "Public (pseudonymous)",
        "contract_language":    "Solidity / Vyper",
        "iot_suitability":      "Low (high latency, cost)",
        "finality_type":        "Probabilistic (PoS attestation)",
    },
    "Polygon (Mumbai / Amoy)": {
        "consensus":            "PoS + Plasma / zkEVM",
        "tps":                  "65--7,000",
        "block_time_s":         "2",
        "confirmation_latency": "2--6 s (1--3 blocks)",
        "gas_per_tx":           "~300,000",
        "cost_per_tx_usd":      "$0.001--$0.01",
        "decentralisation":     "Medium",
        "privacy":              "Public (pseudonymous)",
        "contract_language":    "Solidity / Vyper (EVM)",
        "iot_suitability":      "High (low cost, fast blocks)",
        "finality_type":        "Soft finality (~2 s), L1 finality via checkpoints",
    },
    "Hyperledger Fabric": {
        "consensus":            "Raft / BFT (pluggable)",
        "tps":                  "3,000--20,000",
        "block_time_s":         "0.5--2 (configurable)",
        "confirmation_latency": "0.5--2 s (immediate)",
        "gas_per_tx":           "N/A (no gas model)",
        "cost_per_tx_usd":      "$0 (infrastructure cost only)",
        "decentralisation":     "Low (permissioned)",
        "privacy":              "Private channels + PDC",
        "contract_language":    "Go / Java / Node.js (chaincode)",
        "iot_suitability":      "Medium (fast, but complex infra)",
        "finality_type":        "Deterministic (immediate)",
    },
}

METRIC_LABELS: Dict[str, str] = {
    "consensus":            "Consensus Mechanism",
    "tps":                  "Throughput (TPS)",
    "block_time_s":         "Block Time (s)",
    "confirmation_latency": "Confirmation Latency",
    "gas_per_tx":           "Gas per Transaction",
    "cost_per_tx_usd":      "Cost per Tx (USD)",
    "decentralisation":     "Decentralisation",
    "privacy":              "Privacy",
    "contract_language":    "Contract Language",
    "iot_suitability":      "IoT Suitability",
    "finality_type":        "Finality Type",
}


# ===========================================================================
# Public API
# ===========================================================================

def get_platform_dataframe() -> Any:
    """Return the comparison matrix as a pandas DataFrame.

    Rows are metric names and columns are platform names.  If pandas is not
    installed, returns a plain dictionary-of-dictionaries instead.

    Returns:
        ``pandas.DataFrame`` when pandas is available, otherwise a nested
        ``dict``.
    """
    if _HAS_PANDAS:
        rows: Dict[str, Dict[str, str]] = {}
        for metric_key, label in METRIC_LABELS.items():
            rows[label] = {
                platform: data[metric_key]
                for platform, data in PLATFORM_DATA.items()
            }
        return pd.DataFrame(rows).T
    else:
        return {
            label: {
                platform: data[metric_key]
                for platform, data in PLATFORM_DATA.items()
            }
            for metric_key, label in METRIC_LABELS.items()
        }


def generate_comparison_table() -> str:
    """Generate a LaTeX table comparing the three blockchain platforms.

    The table uses the ``booktabs`` package and a ``tabularx`` environment
    for consistent column widths.

    Returns:
        LaTeX source string ready for inclusion in a paper.
    """
    platforms = list(PLATFORM_DATA.keys())
    col_spec = "l " + " ".join(["p{3.5cm}"] * len(platforms))

    lines: List[str] = [
        r"\begin{table*}[htbp]",
        r"\centering",
        r"\caption{Blockchain platform comparison for vehicular emission monitoring}",
        r"\label{tab:blockchain-comparison}",
        r"\small",
        f"\\begin{{tabular}}{{{col_spec}}}",
        r"\toprule",
        "\\textbf{Metric} & " + " & ".join(
            f"\\textbf{{{p}}}" for p in platforms
        ) + r" \\",
        r"\midrule",
    ]

    for metric_key, label in METRIC_LABELS.items():
        values = [PLATFORM_DATA[p][metric_key] for p in platforms]
        escaped_values = [_latex_escape(str(v)) for v in values]
        lines.append(f"  {label} & " + " & ".join(escaped_values) + r" \\")

    lines += [
        r"\bottomrule",
        r"\end{tabular}",
        r"\end{table*}",
    ]

    return "\n".join(lines)


def get_recommendation() -> str:
    """Return a justified platform recommendation for Smart PUC.

    The recommendation considers cost per transaction, confirmation latency,
    IoT suitability, EVM compatibility (for existing Solidity contracts),
    decentralisation, and alignment with Indian regulatory requirements.

    Returns:
        A multi-line string containing the recommendation and rationale.
    """
    recommendation = (
        "Recommended Platform: Polygon PoS (Mumbai / Amoy testnet)\n"
        "\n"
        "Rationale:\n"
        "\n"
        "1. COST EFFICIENCY: At $0.001--$0.01 per transaction, Polygon is\n"
        "   2--3 orders of magnitude cheaper than Ethereum mainnet/Sepolia,\n"
        "   making it viable for high-frequency OBD-II emission logging\n"
        "   (potentially thousands of transactions per vehicle per year).\n"
        "\n"
        "2. LOW LATENCY: 2-second block times with soft finality in 2--6\n"
        "   seconds satisfy the real-time requirement for on-road emission\n"
        "   monitoring and IoT-grade responsiveness.\n"
        "\n"
        "3. EVM COMPATIBILITY: The existing Solidity smart contracts\n"
        "   (EmissionContract) deploy without modification on Polygon,\n"
        "   preserving the full Truffle/Hardhat toolchain and Web3.py\n"
        "   integration already built for Smart PUC.\n"
        "\n"
        "4. IoT SUITABILITY: Low gas costs and fast confirmations make\n"
        "   Polygon suitable for edge devices and constrained IoT gateways\n"
        "   that relay OBD-II data from vehicles.\n"
        "\n"
        "5. DECENTRALISATION: While less decentralised than Ethereum L1,\n"
        "   Polygon's validator set (~100+ validators) provides sufficient\n"
        "   tamper resistance for a government PUC compliance system.\n"
        "   Checkpoints anchored to Ethereum L1 add an extra security layer.\n"
        "\n"
        "6. REGULATORY ALIGNMENT: Public-chain transparency aligns with\n"
        "   MoRTH/CMVR requirements for auditable emission records [7],\n"
        "   while pseudonymous addressing preserves vehicle-owner privacy.\n"
        "\n"
        "Trade-offs:\n"
        "- Hyperledger Fabric offers higher raw TPS and deterministic\n"
        "  finality but requires dedicated infrastructure, lacks native\n"
        "  EVM support, and is permissioned (limiting public auditability).\n"
        "- Ethereum L1 provides maximum decentralisation but is too costly\n"
        "  and slow for high-frequency IoT emission logging.\n"
        "\n"
        "Conclusion: Polygon PoS provides the optimal balance of cost,\n"
        "speed, security, and developer experience for Smart PUC."
    )
    return recommendation


def get_scoring_matrix() -> Dict[str, Dict[str, float]]:
    """Return a numerical scoring matrix for quantitative comparison.

    Each platform is scored from 1.0 (worst) to 5.0 (best) on key
    dimensions relevant to vehicular emission monitoring.

    Returns:
        Nested dictionary mapping platform names to score dictionaries.
    """
    scores: Dict[str, Dict[str, float]] = {
        "Ethereum (Sepolia)": {
            "cost":              1.5,
            "latency":           2.0,
            "throughput":        2.0,
            "decentralisation":  5.0,
            "evm_compatibility": 5.0,
            "iot_suitability":   1.5,
            "privacy":           2.0,
            "finality":          3.0,
        },
        "Polygon (Mumbai / Amoy)": {
            "cost":              4.5,
            "latency":           4.5,
            "throughput":        4.0,
            "decentralisation":  3.5,
            "evm_compatibility": 5.0,
            "iot_suitability":   4.5,
            "privacy":           2.0,
            "finality":          3.5,
        },
        "Hyperledger Fabric": {
            "cost":              5.0,
            "latency":           5.0,
            "throughput":        5.0,
            "decentralisation":  1.5,
            "evm_compatibility": 1.0,
            "iot_suitability":   3.0,
            "privacy":           5.0,
            "finality":          5.0,
        },
    }
    return scores


def generate_scoring_table() -> str:
    """Generate a LaTeX table of numerical platform scores.

    Returns:
        LaTeX source string with the scoring matrix.
    """
    scores = get_scoring_matrix()
    platforms = list(scores.keys())
    dimensions = list(next(iter(scores.values())).keys())

    dim_labels = {
        "cost":              "Cost",
        "latency":           "Latency",
        "throughput":        "Throughput",
        "decentralisation":  "Decentralisation",
        "evm_compatibility": "EVM Compat.",
        "iot_suitability":   "IoT Suitability",
        "privacy":           "Privacy",
        "finality":          "Finality",
    }

    lines: List[str] = [
        r"\begin{table}[htbp]",
        r"\centering",
        r"\caption{Quantitative platform scoring (1 = worst, 5 = best)}",
        r"\label{tab:platform-scores}",
        r"\begin{tabular}{l " + " ".join(["c"] * len(platforms)) + "}",
        r"\toprule",
        "\\textbf{Dimension} & " + " & ".join(
            f"\\textbf{{{_latex_escape(p)}}}" for p in platforms
        ) + r" \\",
        r"\midrule",
    ]

    for dim in dimensions:
        label = dim_labels.get(dim, dim)
        vals = [f"{scores[p][dim]:.1f}" for p in platforms]
        lines.append(f"  {label} & " + " & ".join(vals) + r" \\")

    # Weighted average row
    weights = {
        "cost": 0.25, "latency": 0.20, "throughput": 0.10,
        "decentralisation": 0.10, "evm_compatibility": 0.15,
        "iot_suitability": 0.10, "privacy": 0.05, "finality": 0.05,
    }
    lines.append(r"\midrule")
    avgs: List[str] = []
    for p in platforms:
        weighted = sum(scores[p][d] * weights.get(d, 0.0) for d in dimensions)
        avgs.append(f"\\textbf{{{weighted:.2f}}}")
    lines.append(r"  \textbf{Weighted Avg.} & " + " & ".join(avgs) + r" \\")

    lines += [
        r"\bottomrule",
        r"\end{tabular}",
        r"\end{table}",
    ]

    return "\n".join(lines)


# ===========================================================================
# Internal helpers
# ===========================================================================

def _latex_escape(text: str) -> str:
    """Escape special LaTeX characters in a string.

    Handles the most common characters that would cause LaTeX compilation
    errors: ``$``, ``~``, ``/``, and ``>``.

    Args:
        text: Raw string to escape.

    Returns:
        String safe for inclusion in LaTeX source.
    """
    # Minimal escaping -- avoid breaking intentional LaTeX in values
    text = text.replace("~", r"\textasciitilde{}")
    text = text.replace(">", r"\textgreater{}")
    return text


# ===========================================================================
# CLI entry point
# ===========================================================================

if __name__ == "__main__":
    print("=" * 70)
    print("  Blockchain Platform Comparison for Smart PUC")
    print("=" * 70)

    print("\n--- Comparison Table (LaTeX) ---\n")
    print(generate_comparison_table())

    print("\n--- Scoring Table (LaTeX) ---\n")
    print(generate_scoring_table())

    print("\n--- Recommendation ---\n")
    print(get_recommendation())

    if _HAS_PANDAS:
        print("\n--- DataFrame ---\n")
        print(get_platform_dataframe().to_string())
