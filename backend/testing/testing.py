"""
Throwaway inspection script — NOT meant to be part of the permanent codebase.

Purpose: see the real column names, dtypes, and a sample row from
nflreadpy's load_snap_counts(), before writing a SQLAlchemy model for it.

Usage: run from anywhere (no project imports needed), e.g.:
    python inspect_snap_counts.py
"""

import nflreadpy as nfl

# Just pull one recent season for inspection — no need to load all 5 yet.
snaps = nfl.load_snap_counts(seasons=[2025])

print("=== Columns ===")
print(snaps.columns)

print("\n=== Schema (column: dtype) ===")
print(snaps.schema)

print("\n=== Row count ===")
print(len(snaps))

print("\n=== Sample rows ===")
# Polars DataFrame -> use head() and iter_rows for readable inspection
for row in snaps.head(5).iter_rows(named=True):
    print(row)