import unittest

from scores import scoreboard_url, normalize


class TestScoresRegressions(unittest.TestCase):

    def test_nba_date_format(self):
        url = scoreboard_url("nba", date="2026-10-11")
        self.assertIn("dates=20261011", url)

    def test_mlb_date_format(self):
        url = scoreboard_url("mlb", date="2026-10-11")
        self.assertIn("dates=20261011", url)

    def test_nested_season_type(self):
        standard = {
            "season": {"type": 3},
            "events": [],
        }
        nested = {
            "season": {"type": {"type": 3, "id": "3"}},
            "events": [],
        }

        expected = normalize(standard, "mlb")["season_type"]
        actual = normalize(nested, "mlb")["season_type"]

        self.assertIsNotNone(expected)
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
