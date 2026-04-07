# SmartPUC — Methodology Notes

This document records design decisions, approximations, and disclosures that
must be reflected accurately in any academic publication built on this codebase.

---

## WLTC Speed Profile

The WLTC speed profile used in this project is a **100-waypoint piecewise-linear
reconstruction** of the UN ECE Regulation No. 154 Annex 1 Class 3b cycle.  Key
characteristics are preserved:

| Property             | Official (R154) | Reconstruction | Error  |
|----------------------|-----------------|----------------|--------|
| Total duration       | 1800 s          | 1800 s         | 0 %    |
| Phase boundaries     | Low / Med / High / Extra-High | Identical | —  |
| Peak speed (Low)     | 56.5 km/h       | 56.5 km/h      | 0 %    |
| Peak speed (Medium)  | 76.6 km/h       | 76.6 km/h      | 0 %    |
| Peak speed (High)    | 97.4 km/h       | 97.4 km/h      | 0 %    |
| Peak speed (Extra-H) | 131.3 km/h      | 131.3 km/h     | 0 %    |
| Total distance       | 23.27 km        | ~23.40 km      | < 0.6% |
| Idle fraction        | ~13 %           | ~11 %          | −2 pp  |

**Known deviations:**

- **Idle fraction:** ~11 % vs official ~13 % (2-point gap due to interpolation
  smoothing that slightly shortens the four Low-phase idle segments at 0–15 s,
  95–105 s, 250–260 s, and 482–496 s).
- **Micro-transients** within phases are averaged out by the 100-waypoint
  resolution.  The reconstruction captures the macro shape of each phase but
  not every 1-second jitter in the official table.

**Implication for published results:** For regulatory-grade certification
testing, the official UN ECE R154 Annex 1 speed table must be used.  This
reconstruction is sufficient for research-grade comparative analysis where the
conclusion depends on cycle-averaged values (g/km totals, CES scores), not on
second-by-second trace fidelity.

Source code: `backend/simulator.py`, function `_generate_wltc_profile()`.

---

## MOVES Emission Rates

The per-bin emission rates in `backend/emission_engine.py` (dict `EMISSION_RATES`)
are **NOT raw EPA MOVES3 BaseRateOutput values**.  They are synthetic calibration
constants tuned to produce BSVI-compliant g/km values across the WLTC cycle for
a representative 1.0–1.2 L naturally-aspirated petrol engine.

The magnitude ranges are informed by published MOVES3 data (EPA-420-B-20-052,
2020) but are not direct copies.  For peer review, these should be described as
**"representative rates calibrated to BSVI certification ranges"**, not as
"EPA MOVES3 emission rates".

Source code: `backend/emission_engine.py`, module-level `DISCLOSURES` §2.

---

## Composite Emission Score (CES) Weights

The CES weighting scheme is **author-proposed** and is NOT a regulatory standard.
ARAI and MoRTH do not specify a multi-pollutant composite score for BS-VI; the
gazette uses binary per-pollutant pass/fail.

### Weight values

| Pollutant | Weight | Rationale |
|-----------|--------|-----------|
| CO₂       | 0.35   | Largest contributor to climate impact; dominant by mass |
| NOx       | 0.30   | NOx contributes more to tropospheric ozone formation and has the strictest BSVI limit (0.06 g/km vs 1.0 g/km for CO). Weighting reflects health-impact severity per WHO Air Quality Guidelines 2021 |
| CO        | 0.15   | Significant health hazard but higher absolute threshold allows more tolerance |
| HC        | 0.12   | Precursor to ground-level ozone; moderate health impact |
| PM2.5     | 0.08   | Highly toxic per unit mass but emitted in very small quantities by petrol engines |

Weights sum to exactly 1.00.  The CES sensitivity analysis (`scripts/ces_sensitivity_analysis.py`)
demonstrates robustness to ±0.05 perturbation of any individual weight.

Source code: `config/ces_weights.json`, `backend/ces_constants.py`.

---

## Comparison with Current Indian PUC Testing

Current Indian PUC centres test only:

- **Petrol vehicles:** CO and HC (idle test at 750 ± 50 RPM)
- **Diesel vehicles:** Smoke opacity (free acceleration test, k-value in m⁻¹)

SmartPUC extends this to **5 pollutants (CO₂, CO, NOx, HC, PM2.5) measured
continuously during real driving**.  This is an *enhancement* over current
practice, not a replication.  The system is designed to be forward-compatible
with anticipated tightening of emission norms under BS-VII (expected 2028–2030).

Key differences from current PUC:

| Aspect | Current PUC | SmartPUC |
|--------|-------------|----------|
| Pollutants tested | 2 (petrol) / 1 (diesel) | 5 |
| Test condition | Idle / free acceleration | Real driving (WLTC/MIDC) |
| Frequency | Once per 6 months | Continuous |
| Tamper resistance | Paper certificate | Blockchain-anchored NFT |
| Fraud detection | None | 4-component ML ensemble |

---

## Blockchain Platform Comparison

The platform comparison in `benchmarks/blockchain_comparison.py` is a
**literature-based comparison table**, not live measurements from SmartPUC
deployments.  TPS, latency, and cost figures are sourced from vendor
documentation and peer-reviewed literature (see references in the module
docstring).

For a published paper, this table should be presented as "published platform
benchmarks" rather than "experimental results measured by our system".

Gas cost measurements in `scripts/measure_gas.js` ARE real measurements from
actual Hardhat transactions.

---

*Last updated: 2026-04-07*
