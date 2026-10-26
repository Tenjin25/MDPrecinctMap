# Maryland SBE unified OpenElections files

Each election has two statewide files with the standard columns:

`county,precinct,office,district,party,candidate,votes,winner`

- `*__precinct.csv` is the atlas input and preserves the finest geography the
  Maryland State Board of Elections publishes.
- `*__county.csv` is a complete county aggregation of that precinct file.
- `manifest.json` records coverage, row counts, and every official source URL.

## Coverage rule

For 2020, 2022, and 2024, SBE reports all vote modes at geographic precinct
level, so the unified file sums those modes within each real precinct.

Before 2020, SBE precinct files contain election-night votes only. Absentee,
provisional, early-voting, and later-canvass votes were published only in the
county files. Those votes are retained under explicit pseudo-precinct labels
such as `ABSENTEE`, `PROVISIONAL`, and `EARLY VOTING`. They are not allocated
to geographic precincts.

The 2002 SBE archive page does not expose precinct CSVs, so it is not included
in this folder; the older county-only file remains in the parent directory.

## Rebuild

From the project root:

```powershell
python scripts/fetch_md_sbe_results.py
```

The converter scrapes the official SBE election-data page for each supported
year, unifies the county files, normalizes office names, and writes UTF-8 CSVs.

