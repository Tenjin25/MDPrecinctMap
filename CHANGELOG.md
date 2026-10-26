# Changelog

All notable changes to the Maryland Election Atlas are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). The project has not yet published a stable release.

## [Unreleased]

### Added

- Initial repository documentation and reproducible local-run instructions.
- Python dependency manifest and repository ignore rules.

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

