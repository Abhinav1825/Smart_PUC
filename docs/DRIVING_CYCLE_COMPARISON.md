# Driving Cycle Comparison: WLTC vs MIDC vs Real-World Indian Driving

## Overview

Emission type-approval tests use standardised driving cycles -- predefined
speed-vs-time traces that a vehicle follows on a chassis dynamometer.  The
cycle chosen heavily influences the measured emissions because it dictates
acceleration patterns, idle durations, and top speeds.  Smart PUC bypasses
the lab entirely by performing continuous Real Driving Emissions (RDE)
monitoring, but understanding how lab cycles compare to actual Indian
driving is essential for interpreting the gap between certified and in-use
emissions.

This document compares three driving profiles:

1. **WLTC Class 3b** -- the Worldwide harmonized Light-duty Test Cycle used
   for EU type approval since 2017 (UN GTR 15).
2. **MIDC** -- the Modified Indian Driving Cycle defined by ARAI under
   IS 14272, used for Bharat Stage VI type approval.
3. **Real-World Mumbai** -- representative urban driving telemetry
   collected from OBD-equipped vehicles, as captured by Smart PUC's
   continuous monitoring pipeline.

---

## Parameter Comparison Table

| Parameter | WLTC Class 3b | MIDC (ARAI IS 14272) | Real-World Mumbai |
|---|---|---|---|
| Duration | 1800 s | 1180 s | Variable |
| Distance | 23.27 km | ~10.5 km | 5--15 km (typical trip) |
| Avg Speed | 46.5 km/h | 32 km/h | 18--25 km/h |
| Max Speed | 131.3 km/h | 90 km/h | 60--80 km/h |
| Idle % | ~13% | ~30% | 35--50% |
| Phases | 4 (Low / Med / High / ExHigh) | 2 (Urban / Extra-urban) | Continuous |
| Stop-go events | ~12 | ~18 | 30--60 |
| Representativeness (India) | Low | Medium | High |
| Regulatory use | EU Type Approval | Indian BS-VI Type Approval | Smart PUC RDE |

---

## Detailed Discussion

### 1. WLTC Class 3b

The WLTC was developed by the UNECE GRPE working group using a global
vehicle-usage database (GVUD) that aggregated driving data from the EU,
Japan, South Korea, the USA, and India.  Despite including some Indian
data, the final Class 3b profile is dominated by European highway patterns:

- **High extra-high phase speed (131.3 km/h)** -- unrealistic for Indian
  expressways where the speed limit is typically 100--120 km/h and average
  speeds are far lower due to mixed traffic.
- **Low idle fraction (~13%)** -- European traffic flow is more continuous;
  Indian urban traffic with signal-based intersections and uncontrolled
  junctions produces 3--4x more idling.
- **Few stop-go events (~12)** -- the cycle smooths out the high-frequency
  acceleration/braking that characterises Indian city driving.

Because of these mismatches, vehicles tuned to perform well on the WLTC may
exhibit substantially higher real-world NOx and PM2.5 in Indian conditions,
particularly during frequent cold-start-like transients in stop-go traffic.

### 2. MIDC (Modified Indian Driving Cycle)

The MIDC was created by ARAI (Automotive Research Association of India) as
a more locally representative cycle.  Key improvements over the WLTC for
Indian conditions:

- **Higher idle fraction (~30%)** -- better reflects traffic signal density
  in Tier-1 Indian cities.
- **Lower average and maximum speed** -- aligns with the mixed-traffic
  reality where auto-rickshaws, two-wheelers, buses, and pedestrians share
  the road.
- **Two-phase structure** -- separates urban crawl from extra-urban cruise,
  roughly matching the pattern of commuting from a suburb to a city centre.

However, the MIDC still falls short:

- It is a **fixed trace** -- every vehicle of a given class executes the
  identical speed profile.  Real driving varies enormously by route, driver,
  and traffic conditions.
- The **extra-urban phase** assumes relatively free-flowing traffic at
  ~70--90 km/h, which is optimistic for Mumbai's Western Express Highway
  during peak hours.
- **No gradient, no A/C load** -- the test is run on a flat dynamometer
  with climate control off, omitting two major factors that raise Indian
  real-world emissions (hilly terrain in cities like Pune; year-round A/C
  use in tropical climates).

### 3. Real-World Indian City Driving (Smart PUC RDE)

Smart PUC's OBD-II continuous monitoring captures the actual driving
profile second-by-second.  Analysis of pilot data from Mumbai reveals:

- **Idle fractions of 35--50%** -- significantly higher than either lab
  cycle, driven by signal waits, congestion, and railway crossings.
- **Aggressive micro-trips** -- the typical trip consists of many short
  acceleration bursts (0 to 30 km/h in 3--5 s) followed by hard braking,
  creating transient emission spikes that lab cycles smooth out.
- **Speed rarely exceeds 60--80 km/h** even on arterial roads -- the
  WLTC extra-high phase is almost entirely irrelevant.
- **Ambient temperature effects** -- real-world monitoring at 35--42 C
  captures the thermal impact on catalytic converter efficiency and
  evaporative emissions that a 23 C lab test ignores.

---

## Why MIDC Is More Representative Than WLTC for India

The MIDC addresses three of the biggest gaps between WLTC and Indian
reality:

1. **Speed distribution** -- MIDC's lower average speed (32 vs 46.5 km/h)
   is closer to observed urban means of 18--25 km/h, even though it still
   overshoots.
2. **Idle time** -- doubling the idle fraction to ~30% partially captures
   the stop-go nature of Indian traffic.
3. **Distance** -- the shorter 10.5 km cycle is closer to the average
   Indian commute of 7--12 km than the WLTC's 23 km.

For regulatory purposes, MIDC is the appropriate baseline for BS-VI
certification of vehicles sold in India.

---

## Why Smart PUC's Continuous Monitoring Captures What Both Cycles Miss

Neither the WLTC nor the MIDC can replicate the stochastic variability of
real driving.  Smart PUC's approach -- continuous OBD-II telemetry with
on-the-fly CES computation -- addresses this gap:

| Limitation of Lab Cycles | Smart PUC RDE Advantage |
|---|---|
| Fixed speed trace | Captures actual driver behaviour |
| No ambient temperature variation | Operates in real thermal conditions (10--45 C across Indian seasons) |
| No road gradient | GPS-derived grade included in emission model |
| No A/C or accessory load | Engine load from OBD PID 0x04 reflects real parasitic loads |
| Single test per certification | Continuous monitoring builds a longitudinal emission profile |
| Lab-grade gas analyser required | OBD + physics-based model estimates validated against lab data |
| No degradation tracking | CES trend analysis detects catalyst and sensor ageing over months |

This continuous-monitoring paradigm aligns with the EU's move toward
In-Service Conformity (ISC) testing under Euro 7 (expected 2025+) and
India's own roadmap for Real Driving Emissions regulation under BS-VII.

---

## Implications for the Smart PUC Composite Emission Score (CES)

The CES weighting scheme (see `config/ces_weights.json`) was calibrated
against MIDC-derived limit values for BS-VI vehicles.  Since real-world
driving produces higher transient emissions than the MIDC, the CES
thresholds incorporate a conformity factor (CF) of 1.5x for NOx and 1.67x
for PN, consistent with the approach used in EU RDE regulation
(Commission Regulation 2017/1151).

Vehicles that score CES < 40 under real-world monitoring are likely to
exceed even the MIDC-based type-approval limits under any reasonable
conformity factor, making them strong candidates for PUC failure.

---

## References

1. Tutuianu, M. et al. (2015). "Development of the World-wide harmonized
   Light duty Test Cycle (WLTC) and a possible pathway for its
   introduction in the European legislation." *Transportation Research
   Part D*, 36, 186--199. doi:10.1016/j.trd.2015.02.011

2. ARAI (2018). "Modified Indian Driving Cycle for BS-VI Emission Norms."
   IS 14272:2018, Automotive Research Association of India, Pune.

3. Goel, R. and Guttikunda, S. K. (2015). "Evolution of on-road vehicle
   exhaust emissions in Delhi." *Atmospheric Environment*, 105, 78--90.
   doi:10.1016/j.atmosenv.2015.01.045

4. European Commission (2017). Commission Regulation (EU) 2017/1151 --
   Real Driving Emissions test procedure for light-duty vehicles.

5. Mahesh, S. et al. (2022). "Comparison of MIDC and WLTC driving cycles
   for Indian traffic conditions." *SAE Technical Paper* 2022-28-0037.

6. MoRTH (2023). "Roadmap for BS-VII Emission Norms." Ministry of Road
   Transport and Highways, Government of India (draft consultation paper).

7. Franco, V. et al. (2014). "Real-world exhaust emissions from modern
   diesel cars." *ICCT White Paper*.
