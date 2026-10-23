# Scenario Assumptions & District Profiles

This document details the operational, hydrological, and demographic assumptions underlying the monsoon-flood scenario templates located in `data/scenarios/`.

---

## 1. District Profiles

### Scenario 1: Cachar District Monsoon Flood (`cachar_monsoon_flood.json`)
- **Location**: Cachar District, Southern Assam (Barak River Valley).
- **Hazard Character**: Lowland riverine monsoon flooding characterized by embankment breaches along the Barak River and its tributaries (Sonai, Madhura), resulting in deep, prolonged inundation of urban settlements and tea-garden agricultural plains.
- **Key Geographical Sectors**:
  - `Silchar Urban`: High-density district headquarters with major urban waterlogging, flooded secondary avenues, and severed municipal power and drainage.
  - `Lakhipur Sub-Division`: Rural riverine zone vulnerable to eastern tributary overflows; road access restricted by submerged highway culverts.
  - `Sonai River Corridor`: Agricultural lowlands experiencing active current overflow; road access compromised to 0.1.
  - `Katigorah Border Plain`: Low-lying plain bordering floodplain depressions; road network 100% cut off (road_access 0.0), making all evacuation strictly waterborne.
- **Healthcare Infrastructure**:
  - `Silchar Medical College Hospital` (SMCH): Premier tertiary referral facility equipped with regional trauma and intensive care units.
  - `Lakhipur Civil Hospital`: Sub-divisional secondary facility providing basic stabilization and triage before evacuation to SMCH.

### Scenario 2: Wayanad District Monsoon Flood (`wayanad_monsoon_flood.json`)
- **Location**: Wayanad District, Northern Kerala (Kabini Basin & Western Ghats).
- **Hazard Character**: High-energy monsoon cloudbursts triggering compound hazards: riverine flooding along the Kabini river system compounded by debris flows and localized landslides in steep tea-estate terrains.
- **Key Geographical Sectors**:
  - `Meppadi Valley`: Steep valley basin hit by landslide runoff and swollen streams; ground route severed (road_access 0.1).
  - `Vythiri High Range`: Mountainous pass with recurring slope failures and blocked ghat roads (road_access 0.3).
  - `Mananthavady Riverine`: River basin lowlands submerged by rising river waters; transport requires combination of high-axle trucks and rescue dinghies.
  - `Sulthan Bathery Lowland`: Plateaus and lowlands retaining partial highway connectivity (road_access 0.6) suitable for logistics staging.
- **Healthcare Infrastructure**:
  - `Wayanad District Hospital Mananthavady`: Primary district hospital with surgical suites and critical care wards.
  - `Sulthan Bathery Taluk Headquarters Hospital`: Decentralized primary/secondary center handling non-critical and stabilization admissions.

---

## 2. Demographic & Epidemiological Assumptions

1. **Vulnerability & Stranded Classification**:
   - "Stranded residents" are defined as individuals unable to self-evacuate due to surrounding floodwaters exceeding 1 meter depth, cut road networks, or structural risks.
   - Stranded ratios range between 18% and 39% of zone populations in high-risk zones.
2. **Critical Patients Ratio**:
   - Approximately 1.5% to 3.5% of stranded residents have acute medical emergencies requiring immediate clinical evacuation (trauma, dialysis, respiratory distress, oxygen dependency, or obstetric emergencies).
   - These patients cannot survive in unassisted field shelter environments past the 2–4 hour window.

---

## 3. Road Access & Transport Feasibility Index

The `road_access` metric is a normalized float ($0.0 \le \text{access} \le 1.0$):
- **1.0**: Uncompromised; all motorized vehicles (standard ambulances, heavy logistics trucks) operate at design speeds.
- **0.5 – 0.7**: Moderate inundation (water depth $\le 0.3\,\text{m}$); passable only by multi-axle 4x4 high-clearance logistics trucks.
- **0.2 – 0.4**: Severe flooding (depth $0.5\,\text{m} - 1.2\,\text{m}$) or minor debris; ground vehicle transit impassable; shallow-draft rescue boats required.
- **0.0 – 0.1**: Complete structural road collapse, bridge severed, or continuous floodwater depth $> 1.5\,\text{m}$; accessible strictly via boats or airlift.

---

## 4. Resource Allocation Tension & Negotiation Design

To ensure genuine multi-agent negotiation dynamics and resource conflict:
- **Boats Quota**: Total available rescue boats (10–12 units) fall significantly below the aggregate demand if Medical (critical patient medevac), Shelter (mass civilian rescue), and Logistics (waterborne supply delivery) each claim their ideal capacity simultaneously.
- **Trucks Quota**: Total logistics trucks (8–14 units) are constrained, compelling the Logistics Agent to prioritize between clear-road food/water transit hubs and secondary staging corridors.
- **Mediator Objective**: Forces agents to negotiate concessions, utilize phased waves, propose alternate fallback routes, and leverage peer estimates.

## 5. Unverified Wayanad operations inputs

The scenario's operations values are demo assumptions, not verified district
fleet or route records. IFRC early-action materials support evacuation planning
and prioritizing vulnerable people, but do not specify vehicle capacity or
local journey times. See the source and per-value rationale in
`../../docs/ASSUMPTIONS.md`.
