# Maryland boundary sources

- `SB1012-2022-Md-Congress-BEQ.xlsx` is the block-equivalency workbook for Maryland's enacted 2022 congressional plan.
- `MCRC-2021-Final-Congressional-BEF.xlsx` is the block-equivalency workbook for the final congressional plan recommended to Governor Larry Hogan by the Maryland Citizens Redistricting Commission on November 5, 2021.
- `MCRC-2021-Final-Congressional-Shapefile.zip` is the matching official MCRC GIS download.
- `tl_2024_24_cd119.zip` is the Census TIGER/Line representation of Maryland's enacted congressional districts for the 119th Congress.
- `tl_2020_24_county20.zip` is the official Census TIGER/Line 2020 county shapefile for Maryland (state FIPS 24). It is the canonical source for the generated `md_counties.geojson` runtime layer and matches the VTD20/block crosswalk vintage.

Run `scripts/build_md_boundaries.py` to regenerate the county, congressional,
House of Delegates, and State Senate GeoJSON files used by the atlas. The
builder reads county boundaries from `tl_2020_24_county20.zip`. Legislative boundaries are read
from `2022HOUSEOFFICIAL.zip` and `2022SENATEOFFICIAL.zip`.

Run `scripts/build_md_bef_crosswalks.py` to regenerate the precinct-to-district
crosswalks in `crosswalks_bef/`. The script joins each official BEF to the
precinct IDs in `md_2020_gen_2020_blocks.zip` by 2020 Census block GEOID, then
weights split precincts by Census block land area from
`tl_2020_24_tabblock20.zip`. The enacted 2022 congressional crosswalk is reused
for the 2024 lines; the MCRC crosswalk represents the commission's 2021 proposal.

Run `scripts/build_md_district_aggregates.py` to aggregate the available RDH
block-level election files directly into the official BEF districts. Enacted
congressional and legislative results are written to `district_contests/`;
the MCRC congressional comparison is written to `district_contests_2021/`.
Each directory contains lazy-loadable contest JSON slices and a manifest. The
builder reconciles every county/contest/party bucket to canonical SBE county
totals, preserves whole-county components exactly, allocates only split-county
components with block evidence, and applies largest-remainder rounding within
each county before district totals are summed.

For the historical proof of concept, run
`scripts/build_md_2008_vtd_crosswalks.py` followed by
`scripts/build_md_2008_sbe_president_proxy.py`. These use the 24 county-level
2008 TIGER VTD files, 2000 Census blocks, both NHGIS block relationship files,
and the modern BEFs. Outputs under `crosswalks_2008_vtd/` and
`district_contests_historical_proxy/2008/` are deliberately marked
provisional because the initial VTD-to-block allocation currently uses block
land area rather than 2000 block voting-age population.
Pass `--promote` to the SBE proxy builder to add its enacted-plan slices to the
main `district_contests/` manifest while retaining the provisional and coverage
metadata.

`scripts/build_md_2000s_sbe_aggregates.py --promote` extends that workflow to
nine statewide contests from 2000, 2004, 2006, and 2008. Every slice records
`vtd_vintage: 2008`; when additional VTD vintages are added, the same allocator
can route each election to its closest geography and replace these proxies.

For the 2010-vintage files, run `scripts/build_md_2012_vtd_crosswalks.py`
followed by `scripts/build_md_2010s_sbe_aggregates.py --promote`. This uses the
2012 VTD and 2010 tabblock files for the 2010, 2012, and 2014 SBE contests and
records `vtd_vintage: 2012` in every output.

Run `scripts/build_md_actual_legislative_results.py` after the aggregation
builders. It replaces reallocated U.S. House data with direct SBE district
returns for 2022 and 2024 and adds the actual 2022 House of Delegates and State
Senate results. U.S. House slices are intentionally limited to the enacted
congressional lines and are not copied to other chambers or the 2021 MCRC plan.

Official sources:

- MCRC final maps and downloads: https://redistricting.maryland.gov/Pages/final.aspx
- Census TIGER/Line congressional districts: https://www2.census.gov/geo/tiger/TIGER2024/CD/
- Census TIGER/Line counties: https://www2.census.gov/geo/tiger/TIGER2024/COUNTY/

## 2026 ballot note

Maryland Question 3 on the November 3, 2026 ballot is a constitutional amendment about the legal criteria and judicial review for congressional redistricting. The final ballot question does not enact or submit a district-boundary plan, so there is no ballot-plan block-equivalency file to add. House Bill 488 contained a proposed 2026 map earlier in the legislative process, but that map is not the plan presented by the final ballot question.

Official ballot text: https://www.elections.maryland.gov/elections/2026/ballot_questions.html
