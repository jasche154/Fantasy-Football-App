#!/usr/bin/env python3
"""
Combine Movement Score — Phase 1A Exploratory Analysis

Extracts 40-yard dash tracking features for all 510 combine participants,
compares tracking-derived times with official combine results, and produces
position-standardized movement features.

This is the ONLY feasible analysis on the Big Data Bowl 2027 dataset for
movement quality assessment. RB-specific analysis is limited to 1 player.
"""

import pandas as pd
import numpy as np
import os
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data" / "nfl-big-data-bowl-2027"
OUTPUT_DIR = Path(__file__).parent / "analysis_output"
OUTPUT_DIR.mkdir(exist_ok=True)


def load_data():
    """Load all required datasets."""
    combine_tracking = pd.read_csv(DATA_DIR / "combine_tracking.csv")
    combine_results = pd.read_csv(DATA_DIR / "combine_results.csv")
    players = pd.read_csv(DATA_DIR / "players.csv")
    return combine_tracking, combine_results, players


def extract_forty_dash_features(combine_tracking):
    """
    Extract movement features from 40-yard dash tracking data.
    
    Tracking is at 10Hz (0.1 sec intervals). The dash is run along the x-axis
    (y stays roughly constant). We compute:
    - Tracking-derived 10yd, 20yd, 40yd split times
    - Peak speed, peak acceleration
    - Acceleration profile smoothness
    - Time to reach 90% of peak speed
    """
    forty = combine_tracking[combine_tracking["drill_name"] == "FORTY_YARD_DASH"].copy()
    
    # Convert time to datetime for proper sorting
    forty["time_dt"] = pd.to_datetime(forty["time"])
    forty = forty.sort_values(["nfl_id", "attempt", "time_dt"])
    
    features_list = []
    
    for (nfl_id, attempt), group in forty.groupby(["nfl_id", "attempt"]):
        group = group.reset_index(drop=True)
        
        # Find start (first frame where speed > threshold)
        start_idx = group[group["s"] > 0.5].index.min()
        if pd.isna(start_idx):
            continue
        start_idx = int(start_idx)
        
        # Find finish (first frame where x >= 40 yards from start)
        start_x = group.loc[start_idx, "x"]
        finish_idx = group[group["x"] >= start_x + 40].index.min()
        if pd.isna(finish_idx):
            # Use last frame if never reaches 40yd marker
            finish_idx = len(group) - 1
        finish_idx = int(finish_idx)
        
        dash = group.iloc[start_idx:finish_idx+1].copy()
        if len(dash) < 10:  # Need minimum frames
            continue
        
        dash["t_rel"] = (dash["time_dt"] - dash["time_dt"].iloc[0]).dt.total_seconds()
        dash["x_rel"] = dash["x"] - dash["x"].iloc[0]
        
        # Official split distances (in yards from start)
        splits = [10, 20, 40]
        split_times = {}
        for split in splits:
            idx = dash[dash["x_rel"] >= split].index.min()
            if not pd.isna(idx):
                split_times[f"tracking_{split}yd"] = dash.loc[int(idx), "t_rel"]
            else:
                split_times[f"tracking_{split}yd"] = np.nan
        
        # Peak speed and acceleration
        peak_speed = dash["s"].max()
        peak_accel = dash["a"].max()
        peak_decel = dash["a"].min()
        
        # Time to 90% peak speed
        speed_90 = 0.9 * peak_speed
        idx_90 = dash[dash["s"] >= speed_90].index.min()
        time_to_90 = dash.loc[int(idx_90), "t_rel"] if not pd.isna(idx_90) else np.nan
        
        # Acceleration smoothness: std of acceleration during acceleration phase
        accel_phase = dash[dash["a"] > 0]
        accel_smoothness = accel_phase["a"].std() if len(accel_phase) > 2 else np.nan
        
        # Total time
        total_time = dash["t_rel"].iloc[-1]
        
        features_list.append({
            "nfl_id": nfl_id,
            "attempt": attempt,
            "tracking_40yd_time": total_time,
            "tracking_10yd_time": split_times.get("tracking_10yd", np.nan),
            "tracking_20yd_time": split_times.get("tracking_20yd", np.nan),
            "tracking_40yd_split": split_times.get("tracking_40yd", np.nan),
            "peak_speed": peak_speed,
            "peak_acceleration": peak_accel,
            "peak_deceleration": peak_decel,
            "time_to_90pct_peak_speed": time_to_90,
            "acceleration_smoothness": accel_smoothness,
            "n_frames": len(dash),
        })
    
    return pd.DataFrame(features_list)


def compare_with_official(tracking_features, combine_results):
    """Compare tracking-derived times with official combine results."""
    # Get official times
    official = combine_results[["nfl_id", "ten_yd_split", "forty", "combine_position"]].copy()
    official.columns = ["nfl_id", "official_10yd", "official_40yd", "combine_position"]
    
    # Average tracking features across attempts per player
    tracking_avg = tracking_features.groupby("nfl_id").agg({
        "tracking_10yd_time": "mean",
        "tracking_20yd_time": "mean", 
        "tracking_40yd_time": "mean",
        "tracking_40yd_split": "mean",
        "peak_speed": "max",
        "peak_acceleration": "max",
        "peak_deceleration": "min",
        "time_to_90pct_peak_speed": "min",
        "acceleration_smoothness": "mean",
        "n_frames": "sum",
        "attempt": "count",
    }).reset_index()
    tracking_avg.rename(columns={"attempt": "n_attempts"}, inplace=True)
    
    # Merge
    merged = tracking_avg.merge(official, on="nfl_id", how="left")
    
    # Compute differences
    merged["diff_10yd"] = merged["tracking_10yd_time"] - merged["official_10yd"]
    merged["diff_40yd"] = merged["tracking_40yd_time"] - merged["official_40yd"]
    merged["pct_diff_40yd"] = 100 * merged["diff_40yd"] / merged["official_40yd"]
    
    return merged


def standardize_by_position(merged, players):
    """Standardize features within position groups."""
    # Use NFL position from players table
    pos_map = players.set_index("nfl_id")["nfl_position"].to_dict()
    merged["nfl_position"] = merged["nfl_id"].map(pos_map)
    
    # Features to standardize
    feature_cols = [
        "tracking_10yd_time", "tracking_20yd_time", "tracking_40yd_time",
        "peak_speed", "peak_acceleration", "peak_deceleration",
        "time_to_90pct_peak_speed", "acceleration_smoothness"
    ]
    
    # Compute z-scores within position
    for col in feature_cols:
        if col in merged.columns:
            merged[f"{col}_z"] = merged.groupby("nfl_position")[col].transform(
                lambda x: (x - x.mean()) / x.std() if x.std() > 0 else 0
            )
    
    # Composite Movement Score (equal weight for now)
    z_cols = [f"{c}_z" for c in feature_cols if f"{c}_z" in merged.columns]
    # Lower is better for time-based metrics, higher for speed/accel
    time_z = [c for c in z_cols if "time" in c or "smoothness" in c]
    speed_z = [c for c in z_cols if c not in time_z]
    
    merged["movement_score"] = (
        merged[speed_z].mean(axis=1) - merged[time_z].mean(axis=1)
    ) if time_z and speed_z else np.nan
    
    # Percentile within position
    merged["movement_pctile"] = merged.groupby("nfl_position")["movement_score"].transform(
        lambda x: x.rank(pct=True) * 100
    )
    
    return merged


def main():
    print("Loading data...")
    combine_tracking, combine_results, players = load_data()
    
    print("Extracting 40-yard dash features...")
    tracking_features = extract_forty_dash_features(combine_tracking)
    print(f"  Extracted features for {tracking_features['nfl_id'].nunique()} players, "
          f"{len(tracking_features)} attempts")
    
    print("Comparing with official combine results...")
    merged = compare_with_official(tracking_features, combine_results)
    
    print("Standardizing by position...")
    merged = standardize_by_position(merged, players)
    
    # Save outputs
    tracking_features.to_csv(OUTPUT_DIR / "forty_dash_tracking_features.csv", index=False)
    merged.to_csv(OUTPUT_DIR / "combine_movement_scores.csv", index=False)
    
    # Summary stats
    print("\n=== SUMMARY ===")
    print(f"Players with tracking features: {len(merged)}")
    print(f"Players with official 40yd: {merged['official_40yd'].notna().sum()}")
    print(f"Mean 40yd diff (tracking - official): {merged['diff_40yd'].mean():.3f} sec")
    print(f"Mean |40yd diff|: {merged['diff_40yd'].abs().mean():.3f} sec")
    print(f"Correlation (tracking vs official 40yd): "
          f"{merged['tracking_40yd_time'].corr(merged['official_40yd']):.3f}")
    
    print("\nBy position:")
    for pos in sorted(merged["nfl_position"].dropna().unique()):
        sub = merged[merged["nfl_position"] == pos]
        n_official = sub["official_40yd"].notna().sum()
        if n_official > 0:
            corr = sub["tracking_40yd_time"].corr(sub["official_40yd"])
            mean_diff = sub["diff_40yd"].mean()
            print(f"  {pos}: n={len(sub)}, official={n_official}, "
                  f"corr={corr:.3f}, mean_diff={mean_diff:.3f}")
    
    # Show top/bottom movement scores
    print("\nTop 10 Movement Scores:")
    top = merged.nlargest(10, "movement_pctile")[
        ["nfl_id", "nfl_position", "movement_score", "movement_pctile", "tracking_40yd_time", "peak_speed"]
    ]
    print(top.to_string(index=False))
    
    print("\nBottom 10 Movement Scores:")
    bot = merged.nsmallest(10, "movement_pctile")[
        ["nfl_id", "nfl_position", "movement_score", "movement_pctile", "tracking_40yd_time", "peak_speed"]
    ]
    print(bot.to_string(index=False))
    
    print(f"\nOutputs saved to {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()