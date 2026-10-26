# Maryland Election Atlas

An interactive, static election atlas for exploring Maryland statewide results by county, precinct, congressional district, House of Delegates district, and State Senate district.

The application is a single-page Mapbox GL site backed by generated GeoJSON, CSV, and JSON assets. It does not require a server-side application or database.

## Current coverage

- Statewide general-election contests from 2000 through 2024, with availability varying by office and year.
- County and precinct results sourced from Maryland State Board of Elections returns.
- Congressional comparisons for the enacted 2022 plan and the Maryland Citizens Redistricting Commission's 2021 recommended plan.
- House of Delegates and State Senate results on the enacted 2022 lines.
- 2022 governor, U.S. Senate, comptroller, and attorney general returns aggregated from SBE precinct results to each modern district layer.
- Historical district estimates routed through the closest available VTD vintage and clearly marked as provisional.
- County, precinct, and district CVAP context where aggregate files are available.

## Run locally

Serve the repository over HTTP; browsers will not reliably load the data files directly from `file://` URLs.

```powershell
python -m http.server 8000
```

Then open <http://localhost:8000/>.

The checked-in public Mapbox token is used by default. A different public token can be supplied before the application initializes through `window.MAPBOX_TOKEN`, or stored in the browser under `mapbox_token`.

## District aggregation

District results use a county-constrained method modeled on the NCPrecinctMap workflow:

1. Canonical Maryland SBE totals are calculated for each county, contest, and party bucket.
2. A county wholly contained in one district contributes its exact county total.
3. For a split county, block or VTD evidence determines the distribution among districts.
4. Largest-remainder rounding is performed independently within each county and party bucket.
5. County contributions are summed into district totals.

This preserves official county and statewide totals while limiting geographic estimation to split-county components. Modern result metadata lists `exact_components` and `allocated_components` for auditability.

For 2022 statewide offices, the pipeline joins SBE precinct codes to the 2020-block official-BEF crosswalks. Direct geographic vote coverage is approximately 97.6%; precincts that changed after 2020 and non-geographic early/absentee/provisional buckets use county-local allocation evidence. Whole-vote largest-remainder rounding preserves every official county/party total. The same method supplies enacted congressional, House of Delegates, State Senate, and 2021 MCRC comparison slices.

Other statewide years with SBE precinct data are already represented through the closest available 2008, 2012, or 2020 VTD/block chain. The county-only 2002 governor result remains available at the county layer because no corresponding precinct file is present in the normalized SBE archive.

## Repository layout

- `index.html` — application, styling, map configuration, and client-side behavior.
- `data/contests/` — lazy-loaded county and precinct contest slices.
- `data/district_contests/` — enacted-plan district slices and manifest.
- `data/district_contests_2021/` — 2021 MCRC congressional comparison slices.
- `data/crosswalks_*` — generated geographic allocation tables.
- `data/openelections/md_sbe/` — normalized SBE county and precinct returns used by the application.
- `scripts/` — acquisition, normalization, crosswalk, boundary, and aggregation scripts.
- `data/README.md` — detailed boundary sources and rebuild notes.

## Python setup

Python 3.11 or newer is recommended.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Raw source archives, official workbooks, PDFs, temporary OCR files, and vendored packages are intentionally excluded from Git. Place required source downloads in `data/` before running the corresponding builders. Existing generated runtime assets remain versioned so the atlas can run immediately after cloning.

## Rebuild workflow

The full pipeline is intentionally explicit rather than hidden behind one destructive command. Run only the stages whose inputs are present:

```powershell
python scripts/build_md_boundaries.py
python scripts/build_md_bef_crosswalks.py
python scripts/build_md_district_aggregates.py
python scripts/build_md_2000s_sbe_aggregates.py --promote
python scripts/build_md_2012_vtd_crosswalks.py
python scripts/build_md_2010s_sbe_aggregates.py --promote
python scripts/build_md_2022_sbe_aggregates.py --promote
python scripts/build_md_actual_legislative_results.py
python scripts/build_md_county_precinct_contests.py
python scripts/sync_md_district_candidate_names.py
```

See [data/README.md](data/README.md) for source-specific details and provenance.

## Validation

```powershell
python scripts/test_md_county_constrained.py
python scripts/validate_md_openelections.py
```

The district builders also stop if block coverage is incomplete or if county-constrained totals fail to conserve votes.

## Data caveats

- Historical district results are estimates when election-vintage precincts must be projected through later VTD and block relationships.
- Non-geographic vote buckets such as early, absentee, and provisional votes are included in totals and allocated using county-local evidence, but they do not appear as precinct polygons.
- Direct SBE-reported district results supersede geographic estimates when available.

Source organizations include the Maryland State Board of Elections, Maryland Department of Planning/redistricting authorities, the U.S. Census Bureau, NHGIS, and the Redistricting Data Hub. Individual generated files carry more specific provenance metadata.

