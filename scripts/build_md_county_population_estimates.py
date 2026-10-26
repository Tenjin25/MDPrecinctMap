"""Convert the Census Vintage 2025 Maryland county workbook for the atlas."""

from __future__ import annotations

import csv
import re
from pathlib import Path

from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SOURCE = DATA / "co-est2025-pop-24.xlsx"
OUTPUT = DATA / "county_population_estimates_2025.csv"


def clean_name(raw: str) -> str:
    value = str(raw or "").strip().lstrip(".")
    value = re.sub(r",\s*Maryland$", "", value, flags=re.IGNORECASE)
    if value.lower() == "maryland":
        return "Maryland"
    value = re.sub(r"\s+County$", "", value, flags=re.IGNORECASE)
    if value.lower() == "baltimore city":
        return "Baltimore City"
    return value


def main() -> None:
    sheet = load_workbook(SOURCE, read_only=True, data_only=True).active
    rows = []
    for values in sheet.iter_rows(min_row=5, values_only=True):
        raw_name = values[0]
        if not raw_name or values[1] is None:
            continue
        name = clean_name(raw_name)
        if name != "Maryland" and (not name or len(name) > 40):
            continue
        base_2020, july_2020, p2021, p2022, p2023, p2024, p2025 = (int(v) for v in values[1:8])
        change = p2025 - base_2020
        rows.append(
            {
                "name": name,
                "normalized_name": name.upper(),
                "estimate_base_2020": base_2020,
                "estimate_2020": base_2020,
                "estimate_2020_july1": july_2020,
                "estimate_2021": p2021,
                "estimate_2022": p2022,
                "estimate_2023": p2023,
                "estimate_2024": p2024,
                "estimate_2025": p2025,
                "change_2020_to_2025": change,
                "pct_change_2020_to_2025": round(change / base_2020 * 100.0, 6) if base_2020 else 0.0,
                "source": "U.S. Census Bureau Vintage 2025 CO-EST2025-POP-24",
            }
        )
    with OUTPUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {OUTPUT.relative_to(ROOT)} ({len(rows) - 1} counties + Maryland)")


if __name__ == "__main__":
    main()
