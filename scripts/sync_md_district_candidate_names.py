"""Synchronize statewide candidate labels from county JSONs into district slices.

District-specific contests retain their own candidates. For statewide offices,
the county/precinct contest JSON is the canonical display-name source across all
map layers.
"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DISTRICT_DIRS = (DATA / "district_contests", DATA / "district_contests_2021")
DISTRICT_SPECIFIC = {"us_house", "state_house", "state_senate"}


def canonical_candidates(contest_type: str, year: int) -> tuple[str, str] | None:
    path = DATA / "contests" / f"{contest_type}_{year}.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rows") or []
    dem_names = {str(row.get("dem_candidate") or "").strip() for row in rows}
    rep_names = {str(row.get("rep_candidate") or "").strip() for row in rows}
    dem_names.discard("")
    rep_names.discard("")
    if len(dem_names) > 1 or len(rep_names) > 1:
        raise ValueError(f"{path}: county rows contain inconsistent candidate labels")
    return next(iter(dem_names), ""), next(iter(rep_names), "")


def main() -> None:
    changed_files = 0
    changed_rows = 0
    for directory in DISTRICT_DIRS:
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.json")):
            if path.name == "manifest.json":
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            meta = payload.get("meta") or {}
            contest_type = str(meta.get("contest_type") or "").strip()
            year = int(meta.get("year") or 0)
            if not contest_type or not year or contest_type in DISTRICT_SPECIFIC:
                continue
            candidates = canonical_candidates(contest_type, year)
            if candidates is None:
                continue
            dem_candidate, rep_candidate = candidates
            file_changed = False
            for row in (payload.get("general") or {}).get("results", {}).values():
                row_changed = False
                if dem_candidate and row.get("dem_candidate") != dem_candidate:
                    row["dem_candidate"] = dem_candidate
                    row_changed = True
                if rep_candidate and row.get("rep_candidate") != rep_candidate:
                    row["rep_candidate"] = rep_candidate
                    row_changed = True
                if row_changed:
                    changed_rows += 1
                    file_changed = True
            if file_changed:
                path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
                changed_files += 1
                print(f"updated {path.relative_to(ROOT)}")
    print(f"synchronized {changed_rows} rows across {changed_files} files")


if __name__ == "__main__":
    main()
