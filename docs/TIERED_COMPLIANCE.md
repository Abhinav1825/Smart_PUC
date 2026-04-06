# SmartPUC — Tiered Compliance Framework

> **Version:** v4.1 · **Date:** 2026-04-06 · **Status:** Implemented in contracts + backend + tests

## Overview

SmartPUC's Tiered Compliance Framework extends PUC certificate validity from the standard 180 days to **up to 730 days (2 years)** for continuously monitored vehicles that demonstrate sustained emission compliance. This is the system's core argument for replacing or extending traditional 6-month tailpipe PUC testing.

## The Core Argument

A PUC test is a **30-second snapshot once every 180 days** — 1 data point per 15,552,000 seconds of vehicle operation. SmartPUC generates a reading every second during driving. Over 6 months of typical urban driving (~2 hours/day), SmartPUC accumulates **~360,000 data points**.

**Theorem 1 (Statistical Detection Power):** For per-reading detection probability *p* = 0.02 and *N* independent readings, the cumulative detection power is:

    P_detect(N, p) = 1 − (1 − p)^N

This exceeds a single tailpipe test's detection power (P_puc ≈ 0.85) when N ≥ 94 readings — approximately **94 seconds (< 2 minutes) of driving**. See `physics/detection_power.py` for the full proof.

**Monte Carlo Validation (1000 vehicles, 12 months):**

| Metric | Value |
|---|---|
| Mean detection advantage over PUC | **109 days** |
| Vehicles caught before next PUC | **93.2%** |
| Vehicles PUC would never catch | **17.5%** |
| Clean vehicle false-positive rate | **0.14%** |

See `docs/detection_latency_report.json` for the full results.

## Tier Definitions

| Tier | CES Threshold | Min Records | Max Fraud Alerts | PUC Validity | GCT Multiplier |
|---|---|---|---|---|---|
| **Gold** | CES < 0.50 (avg) | 50+ | 0 | **730 days (2 years)** | 1.5× |
| **Silver** | CES < 0.75 (avg) | 20+ | ≤ 1 | **365 days (1 year)** | 1.2× |
| **Bronze** | CES < 1.00 (avg) | 5+ | ≤ 3 | **180 days (standard)** | 1.0× |
| **Unclassified** | Any | < 5 | Any | **180 days (standard)** | 1.0× |

**ALERT tier** (not stored on-chain, triggered by backend):
- CES crosses 0.90 for 14 consecutive days, OR
- Drift detector fires, OR
- Emission-related DTC detected (P04xx catalyst, P01xx fuel, P03xx ignition)
- → Vehicle owner gets immediate retest notification
- → RTO receives flagged-vehicle alert

## How Tiers Are Computed

Tiers are computed **on-chain** in `EmissionRegistry._updateVehicleTier()` at the end of every `storeEmission()` call. The computation is O(1) using stored aggregates:

```
avgCES = cesSumByVehicle[vid] / totalRecords[vid]
fraudAlerts = _fraudAlertCount[vid]
totalRecords = emissionRecords[vid].length
```

The tier is the highest tier whose ALL criteria are met (CES + records + fraud). A vehicle must **earn** its tier through sustained compliance, not a single good reading.

## How Tiers Affect PUC Certificates

When `PUCCertificate.issueCertificate()` is called:

1. **First PUC** (after vehicle registration): always 360 days, regardless of tier (per CMVR Rule 115)
2. **Renewals**: validity = `tierValidityPeriods[tier]`:
   - Gold → 730 days (2 years)
   - Silver → 365 days (1 year)
   - Bronze/Unclassified → 180 days (standard 6 months)

The tier is recorded in the on-chain `CertificateData` struct and emitted in the `TieredCertificateIssued` event.

## AI Calibration Layer

The gap between OBD-inferred emissions and real tailpipe measurements is learned by an XGBoost model trained on paired (OBD, tailpipe) data. See `ml/calibration_model.py`.

**Current performance (synthetic paired data, 10-vehicle test):**

| Pollutant | R² | MAE |
|---|---|---|
| CO₂ | 0.88 | 7.64 g/km |
| CO | 0.91 | 0.10 g/km |
| NOx | 0.55 | 0.01 g/km |
| HC | 0.94 | 0.01 g/km |
| PM2.5 | 0.98 | 0.0002 g/km |

**Disclosure:** Current training is on synthetic paired data generated using COPERT 5 degradation curves. Real-world paired measurements from PUC centers are required before regulatory deployment. The architecture hot-swaps from synthetic to real data without code changes.

## Degradation Detection

Vehicle emission degradation is modeled using published COPERT 5 / NAEI deterioration rates:

    EF(km) = EF_base × (1 + rate_per_km × min(mileage, cap_km))

See `physics/degradation_model.py` and `data/copert5_degradation_rates.json`.

Supported failure modes:
- Catalytic converter aging (gradual CO/HC rise)
- O₂ sensor drift (fuel trim shift → CO₂ change)
- EGR valve failure (NOx spike under load)
- DPF removal/deterioration (PM2.5 spike, diesel)
- Injector fouling (HC rise, efficiency drop)

Detection is via the Page-Hinkley drift detector (already in the fraud ensemble) + DTC reading from OBD-II Mode 03.

## Weekly Micro-Assessment

Every 7 days, `ml/micro_assessment.py` generates a health report per vehicle:
- CES trend (slope per day)
- Per-pollutant breakdown
- Driving behavior score (0–100)
- Degradation risk: low/medium/high
- Projected days to PUC failure
- Actionable recommendations (e.g., "HC rising — inspect spark plugs")

API: `GET /api/vehicle/{vehicle_id}/health-report`

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/vehicle/{id}/tier` | Current compliance tier + validity days |
| GET | `/api/vehicle/{id}/health-report` | Latest weekly health report |
| GET | `/api/vehicle/{id}/degradation` | Degradation risk + projected failure |
| POST | `/api/vehicle/{id}/paired-reading` | Submit OBD+tailpipe pair for calibration |

## Smart Contract Functions

**EmissionRegistry.sol:**
- `getVehicleTier(vehicleId) → uint8` (0=Unclassified, 1=Bronze, 2=Silver, 3=Gold)
- `setVehicleTierManually(vehicleId, tier)` (admin only)
- Event: `VehicleTierUpdated(vehicleId, oldTier, newTier, timestamp)`

**PUCCertificate.sol:**
- `tierValidityPeriods(tier) → uint256` (seconds)
- `setTierValidity(tier, seconds)` (authority only, 90–1095 days range)
- Event: `TieredCertificateIssued(vehicleId, tier, validityDays, tokenId)`
- Event: `TierValidityUpdated(tier, validitySeconds)`

## References

- Wald, A. (1945). Sequential Analysis. Wiley.
- California BAR-OIS Technical Report (2018)
- NAEI Emission Degradation Methodology (2024)
- COPERT 5 v5.6, Emisia
- EEA EMEP/EEA Guidebook 2023, Chapter 1.A.3.b
- Chen, T., Guestrin, C. (2016). XGBoost: A Scalable Tree Boosting System. KDD.
- Page, E. S. (1954). Continuous Inspection Schemes. Biometrika.
