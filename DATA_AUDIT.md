# NFL Big Data Bowl 2027 — Data Audit Report
## Running Back Vision Score Project

**Date:** 2025-10-13  
**Analyst:** Senior Sports Analytics Engineer

---

## 1. Dataset Overview

| File | Rows | Unique NFL IDs | Key Notes |
|------|------|----------------|-----------|
| `combine_results.csv` | 510 | 510 | 2023-2025 draft classes; combine measurements |
| `combine_tracking.csv` | 463,189 | 510 | 10Hz tracking for combine drills |
| `players.csv` | 510 | 510 | Player metadata, positions, draft info |
| `player_play.csv` | 314,197 | 497 | Per-play stats for 2023-2025 seasons |
| `player_career_successes.csv` | 510 | 510 | Career snap counts, honors |
| `games.csv` | 1,002 | — | Game metadata |
| `game_tracking_2023.csv` | 3.67M | 156 | 10Hz game tracking |
| `game_tracking_2024.csv` | 7.83M | 318 | 10Hz game tracking |
| `game_tracking_2025.csv` | 11.16M | 448 | 10Hz game tracking |

---

## 2. Running Back Coverage — Critical Finding

### Only **1 running back** exists in the entire dataset:

| NFL ID | Name | Draft Year | Combine Position | NFL Position | Combine Results |
|--------|------|------------|------------------|--------------|-----------------|
| 57253 | Sione Vaki | 2024 | **DB** | **RB** | Yes (listed as DB) |

**Implications:**
- Vaki was a college RB/DB hybrid (Utah); NFL lists him as RB, combine listed him as DB
- **Zero traditional RBs** (by combine_position) in the dataset
- No three-cone drill, short shuttle, or RB-specific combine drills for any RB
- Vaki's combine tracking only includes: 40-yard dash (2 attempts), DB skill drills (gauntlet, W-drill, line drill, back-pedal transitions, box drill, Teryl Austin drills)

---

## 3. Combine Drills Available

### Drill Types in `combine_tracking.csv`:

| Drill Type | Frames | Players | Notes |
|------------|--------|---------|-------|
| `SKILL_DRILLS_WR` | 120,383 | ~107 | Route running, gauntlet, etc. |
| `SKILL_DRILLS_DB` | 94,623 | ~122 | Back-pedal, transitions, W-drill |
| `SKILL_DRILLS_DL` | 77,428 | ~118 | Pass rush, bag drills |
| `SKILL_DRILLS_OL` | 61,129 | ~121 | Pull drills, fold blocks |
| `FORTY_YARD_DASH` | 41,500 | **510** | All positions; 10Hz tracking |
| `SKILL_DRILLS_TE` | 37,222 | ~42 | TE routes |
| `THREE_CONE_DRILL` | 15,951 | ~118 | **Only 118 players** (mostly DL/OL/LB) |
| `SHORT_SHUTTLE` | 11,690 | ~118 | **Only 118 players** |
| `SKILL_DRILLS_LB` | 3,263 | ~2 | Minimal LB coverage |

### RB-Specific Combine Tracking:
- **40-yard dash**: 2 attempts, 103 frames total (10Hz → ~10 sec coverage)
- **NO three-cone drill** for any RB
- **NO short shuttle** for any RB
- **NO RB skill drills** (only DB drills for Vaki)

---

## 4. Game Tracking — Rushing Plays

### Rush Plays in `player_play.csv` (2023-2025):
- **Total rush plays: 22** (all by Sione Vaki, nfl_id=57253)
- **All 22 plays are from 2024-2025 preseason games** (August 2024, November 2024, August 2025)
- **Zero regular-season rush plays** for any RB
- Rush yards range: -2 to +15 yards

### Tracking Coverage for Rush Plays:

| Season | Rush Plays | With Tracking Overlap | Players Tracked per Play | Frames per Play (RB) |
|--------|------------|----------------------|-------------------------|---------------------|
| 2023 | 0 | 0 | — | — |
| 2024 | 22 | 16 | 1-5 (avg ~3) | 59-90 (6-9 sec) |
| 2025 | 0* | 6 | 2-4 | 56-69 (5-7 sec) |

*2025 rush plays in player_play are from August 2025 preseason (game_ids starting 202508...)

### Tracking Data Structure (per play):
- **Events recorded**: `ball_snap`, `handoff`, `first_contact`, `tackle` (only 4 events per play)
- **Players tracked**: Typically 3-5 (RB + QB + 1-3 defenders/blockers)
- **Frame rate**: 10Hz (0.1 sec intervals)
- **Coordinates**: x, y (field coordinates in yards), s (speed), a (acceleration), dis (distance), o (orientation), dir (direction)

---

## 5. What CAN Be Measured (With Major Caveats)

### ✅ Combine Movement Analysis (Phase 2) — **Partially Feasible**
| Feature | Feasibility | Notes |
|---------|-------------|-------|
| 40-yard dash segment times | ✅ Yes | 2 attempts for 1 RB; 510 players for comparison |
| Speed/acceleration curves | ✅ Yes | 10Hz tracking available for all 40-yard dashes |
| Peak acceleration/deceleration | ✅ Yes | Derivable from `s` and `a` columns |
| Time to regain speed after turn | ❌ No | No three-cone/short-shuttle for RBs |
| Change-of-direction duration | ❌ No | No three-cone/short-shuttle for RBs |
| Angular velocity | ⚠️ Limited | Only from 40-yard dash (straight line) |
| Distance vs reference path | ⚠️ Limited | 40-yard dash is straight; no drill geometry for RBs |

**Movement Score** can be computed for **all 510 players** using 40-yard dash tracking, but **RB-specific** analysis is limited to **1 player**.

### ✅ Game Tracking Features — **Extremely Limited Sample**
| Feature | Feasibility | Notes |
|---------|-------------|-------|
| Hole recognition | ❌ No | Only 3-5 players tracked; no full OL/DL picture |
| Patience/timing | ⚠️ Minimal | 60-90 frames from snap to tackle; no pre-snap motion |
| Blocking synchronization | ❌ No | Only 1-2 blockers tracked per play |
| Adaptability | ❌ No | No lane visualization; single RB, 22 plays |
| RYOE comparison | ❌ No | No expected yards model; only 22 plays |

---

## 6. What CANNOT Be Measured Reliably

| Vision Score Component | Feasibility | Reason |
|------------------------|-------------|--------|
| **Hole Recognition** | ❌ Impossible | Tracking covers only 3-5 players/play; no lane detection possible |
| **Patience and Timing** | ❌ Impossible | No pre-snap tracking; only snap→handoff→contact→tackle events |
| **Blocking Synchronization** | ❌ Impossible | At most 1-2 OL tracked; no blocking scheme labels |
| **Adaptability** | ❌ Impossible | No defensive reaction data; no alternative lane visibility |
| **Play-level grading (0-4 rubric)** | ❌ Impossible | Insufficient context; requires human annotation of full play |

---

## 7. Prioritized Implementation Plan

### Phase 1A: Combine Movement Score (High Value, Feasible)
**Target:** All 510 combine participants using 40-yard dash tracking
- Extract 10-yard split, 20-yard split, 40-yard time from tracking
- Compute speed/acceleration profiles
- Compare tracking-derived vs official combine times
- Standardize by position group (WR, DB, TE, OL, DL, LB, RB*, QB*)
- *RB group = 1 player (Vaki); treat as outlier

**Deliverable:** `combine_movement_score.py` — position-standardized movement features

### Phase 1B: Preseason Rush Play Exploration (Exploratory Only)
**Target:** 22 Vaki plays with tracking
- Visualize RB trajectory relative to tracked defenders/blockers
- Compute: time to line of scrimmage, acceleration at contact, path deviation
- **Explicitly label as "N=1, preseason only — not generalizable"**

### Phase 2: External Data Integration (Required for Vision Score)
**Needed to proceed:**
1. **nflverse play-by-play data** — regular season rushes for all RBs (2021-2024)
2. **Next Gen Stats (NGS) rushing metrics** — RYOE, rush yards over expected, efficiency
3. **College tracking data** — if available for 2023-2025 RB classes
4. **Full game tracking** — current dataset only has subset of plays/players

---

## 8. Recommendation

### **Do not proceed** with Vision Score development on this dataset alone.

**The dataset is fundamentally mismatched to the Vision Score goal:**
- Designed for **combine athletic testing** (40-yard dash, position drills)
- Game tracking is a **small subset** (preseason, limited players/play)
- **Zero regular-season RB rushes** with tracking
- **No lane/blocking/defensive structure** in tracking data

### **Immediate Next Steps:**

1. **Build Combine Movement Score** (Phase 1A above) — validates tracking-derived metrics against official combine results for all positions
2. **Acquire nflverse play-by-play + NGS data** — for actual RB rushing production metrics
3. **Request full game tracking** from NFL/nflverse — current dataset is a competition subset
4. **Consider college tracking data** (if Big Data Bowl 2024/2025/2026 have RB coverage)

### **If constrained to this dataset only:**
- Pivot to **"Combine Movement Quality Score"** for all positions
- Use 40-yard dash tracking to measure acceleration smoothness, top-speed maintenance, start efficiency
- This is a **physical execution metric**, not a vision/decision-making metric
- Clearly separate from any football vision claims

---

## Appendix: Key Data Schema Reference

### `combine_tracking.csv` columns:
`draft_year, event_id, nfl_id, entity_type, time, drill_type, drill_name, attempt, x, y, s, a, dis, dir`

### `game_tracking_*.csv` columns:
`game_id, play_id, nfl_id, time, x, y, s, a, dis, o, dir, event`

### Key events in game tracking:
`ball_snap, handoff, first_contact, tackle, pass_forward, pass_arrived, run, run_attempt, qb_sack, touchdown, out_of_bounds, fumble, ...`

### `player_play.csv` rushing-relevant columns:
`rush_yards, yards_gained, lined_up_position, play_description, offense_formation, expected_points_added`