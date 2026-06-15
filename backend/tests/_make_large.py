"""Builds a 100k-row CSV at test time for fixture 12's sampling-path test.
Not committed as a file — generated into a tmp path by the test."""
from pathlib import Path
import csv, random

def make_large_csv(path: Path, rows: int = 100_000, seed: int = 7) -> Path:
    random.seed(seed)
    cats = ["Tools", "Electronics", "Garden", "Office", "Toys"]
    with open(path, "w", newline="") as f:
        wtr = csv.writer(f)
        wtr.writerow(["id", "category", "amount", "qty"])
        for i in range(1, rows + 1):
            wtr.writerow([i, random.choice(cats), round(random.uniform(1, 999), 2), random.randint(1, 50)])
    return path
