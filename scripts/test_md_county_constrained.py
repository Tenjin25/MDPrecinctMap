from __future__ import annotations

import unittest

from md_county_constrained import apportion_counties_to_districts


class CountyConstrainedAllocationTests(unittest.TestCase):
    def test_whole_county_component_is_exact(self) -> None:
        raw = {"001": {"7": {"dem": 9.4, "rep": 5.6, "other": 0.2}}}
        targets = {"001": {"dem": 10, "rep": 6, "other": 1}}
        districts, contributions = apportion_counties_to_districts(raw, targets)
        self.assertEqual(contributions["001"]["7"], targets["001"])
        self.assertEqual(districts["7"], targets["001"])

    def test_split_county_rounds_within_county_and_conserves(self) -> None:
        raw = {
            "001": {
                "1": {"dem": 4.2, "rep": 2.1, "other": 0.0},
                "2": {"dem": 5.8, "rep": 6.9, "other": 0.0},
            }
        }
        targets = {"001": {"dem": 10, "rep": 9, "other": 0}}
        _, contributions = apportion_counties_to_districts(raw, targets)
        for party, target in targets["001"].items():
            self.assertEqual(
                sum(row[party] for row in contributions["001"].values()),
                target,
            )

    def test_mixed_district_sums_exact_and_allocated_components(self) -> None:
        raw = {
            "001": {"1": {"dem": 7, "rep": 3, "other": 1}},
            "003": {
                "1": {"dem": 4, "rep": 1, "other": 0},
                "2": {"dem": 6, "rep": 8, "other": 0},
            },
        }
        targets = {
            "001": {"dem": 7, "rep": 3, "other": 1},
            "003": {"dem": 10, "rep": 9, "other": 0},
        }
        districts, contributions = apportion_counties_to_districts(raw, targets)
        self.assertEqual(contributions["001"]["1"], targets["001"])
        self.assertEqual(
            districts["1"]["dem"],
            targets["001"]["dem"] + contributions["003"]["1"]["dem"],
        )


if __name__ == "__main__":
    unittest.main()
