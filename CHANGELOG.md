# Changelog

All notable changes to the Maryland Election Atlas are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). The project has not yet published a stable release.

## [Unreleased]

### Added

- Initial repository documentation and reproducible local-run instructions.
- Python dependency manifest and repository ignore rules.
- 2022 governor, U.S. Senate, comptroller, and attorney general aggregates for enacted congressional, House of Delegates, and State Senate lines.
- Matching 2022 statewide aggregates for the 2021 MCRC congressional comparison plan.
- A reproducible 2022 SBE precinct-to-official-BEF aggregation builder with coverage and unmatched-precinct diagnostics.
- The 18 previously omitted 2000–2014 statewide contest slices for the 2021 MCRC congressional comparison plan.

### Changed

- District candidate labels now inherit the canonical county-contest names after aggregation, including governor names without running mates.
- The README now distinguishes direct reported legislative results, modern precinct-crosswalk aggregates, historical VTD-chain estimates, and the county-only 2002 governor result.
- Historical builders now promote both enacted and MCRC congressional outputs and support `--mcrc-only` rebuilds.

### Validated

- The new 2022 district slices preserve official county and statewide party totals as whole votes.
- Direct geographic vote coverage is approximately 97.59–97.60%; remaining votes use county-local imputation rather than statewide allocation.
- All 31 MCRC statewide contest-years with precinct data conserve their official Democratic, Republican, other, and total vote counts.

## [0.1.0] - 2026-10-25

### Added

- Interactive Maryland county, precinct, congressional, House of Delegates, and State Senate map views.
- Lazy-loaded statewide contest slices covering available elections from 2000 through 2024.
- Enacted 2022 congressional lines and a comparison with the 2021 MCRC recommended congressional plan.
- Historical VTD-to-modern-district crosswalk workflows using 2008 and 2012 VTD vintages.
- Official SBE-reported legislative and congressional result ingestion where available.
- County, precinct, and district demographic context using CVAP aggregates.
- Search, trend history, margin tiers, shift and flip modes, shareable state, and responsive desktop/mobile interactions.

### Changed

- District aggregation now preserves canonical SBE county/contest/party totals, allocates only split-county components, and rounds by largest remainder within each county before summing districts.
- Modern district slices now record exact whole-county and allocated split-county components in their metadata.
- Historical district builders use the same county-constrained conservation method before promotion.
- Maryland county and enacted legislative boundary paths now use generated Maryland-specific runtime files with consistent lowercase `data/` paths.

### Validated

- All modern block assignments matched their official block-equivalency plans.
- All rebuilt county-constrained district slices conserved county and statewide vote totals.
- County-constrained unit tests and Python compilation checks passed.

