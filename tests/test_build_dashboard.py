"""The dashboard generator must produce a safe, offline, filterable page."""

import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "tools" / "build_dashboard.py"

spec = importlib.util.spec_from_file_location("build_dashboard", SCRIPT)
dashboard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dashboard)


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.jobs = self.tmp / "seen_jobs.json"
        self.tracker = self.tmp / "tracker.csv"
        self.output = self.tmp / "dashboard.html"

        self.jobs.write_text(
            json.dumps(
                {
                    "seen": {
                        "acme_data-engineer": {
                            "title": "Senior Data Engineer",
                            "company": "Acme & <Co>",
                            "url": "https://example.com/jobs/1",
                            "location": "Kuala Lumpur, Malaysia",
                            "posted_date": "2026-11-01",
                            "first_seen": "2026-11-01",
                            "deadline": None,
                            "fit": "high",
                            "status": "new",
                            "portal": "freehire-search",
                            "rank_score": 82,
                        },
                        "beta_data-engineer": {
                            "title": "Data Engineer",
                            "company": "Beta GmbH",
                            "url": "https://example.com/jobs/2",
                            "location": "Berlin, Germany",
                            "posted_date": "2026-10-30",
                            "fit": "medium",
                            "status": "new",
                            "portal": "linkedin-search",
                        },
                        "gamma_skip": {
                            "title": "Junior Data Engineer",
                            "company": "Gamma",
                            "url": "https://example.com/jobs/3",
                            "location": "Bangkok, Thailand",
                            "posted_date": "2026-10-29",
                            "fit": "low",
                            "status": "skipped",
                            "portal": "freehire-search",
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        with self.tracker.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "date", "company", "sector", "role", "role_type", "channel", "status",
                    "contact_person", "fit_rating", "notes", "cv_file", "cover_letter_file",
                    "source", "deadline",
                ]
            )
            writer.writerow(
                [
                    "2026-11-02", "Acme", "Tech", "Senior Data Engineer", "IC", "portal",
                    "applied", "", "5", "applied via site", "cv/a.pdf", "cover/a.pdf",
                    "https://example.com/jobs/1", "2026-11-20",
                ]
            )
            writer.writerow(
                [
                    "2026-11-03", "Beta", "Tech", "Data Engineer", "IC", "referral",
                    "interview", "", "4", "first round", "cv/b.pdf", "cover/b.pdf",
                    "https://example.com/jobs/2", "",
                ]
            )

    def test_build_produces_offline_filterable_dashboard(self):
        result = dashboard.build(self.jobs, self.tracker, self.output)
        self.assertTrue(self.output.is_file())
        page = self.output.read_text(encoding="utf-8")

        self.assertEqual(result["jobs"], 3)
        self.assertEqual(result["eligible"], 2)
        self.assertEqual(result["high"], 1)
        self.assertEqual(result["applications"], 2)
        self.assertEqual(result["interviews"], 1)

        # Escaping: the company name must not inject markup.
        self.assertIn("Acme &amp; &lt;Co&gt;", page)
        self.assertNotIn("<Co>", page)

        # Filters and data attributes.
        self.assertIn('data-country="Malaysia"', page)
        self.assertIn('data-country="Germany"', page)
        self.assertIn('data-fit="high"', page)
        self.assertIn('id="filter-country"', page)
        self.assertIn("applyFilters", page)

        # Application status bucket.
        self.assertIn("Active", page)
        self.assertIn("Interview", page)

        # Single self-contained file: no external script/style/font requests.
        self.assertNotIn("<script src=", page)
        self.assertNotIn("cdn.", page)
        self.assertNotIn("@import", page)

    def test_country_derivation(self):
        self.assertEqual(dashboard.country_for("Kuala Lumpur, Malaysia"), "Malaysia")
        self.assertEqual(dashboard.country_for("Singapore, Singapore"), "Singapore")
        self.assertEqual(dashboard.country_for("Bangkok, Thailand"), "Thailand")
        self.assertEqual(dashboard.country_for("Dubai, United Arab Emirates"), "UAE")
        self.assertEqual(dashboard.country_for("Berlin, Germany"), "Germany")
        self.assertEqual(dashboard.country_for("Remote - Europe"), "Remote")
        self.assertEqual(dashboard.country_for("Somewhere else"), "Other")

    def test_build_survives_missing_files(self):
        empty = self.tmp / "missing.json"
        result = dashboard.build(empty, self.tmp / "missing.csv", self.output)
        self.assertEqual(result["jobs"], 0)
        self.assertEqual(result["applications"], 0)
        self.assertTrue(self.output.is_file())


if __name__ == "__main__":
    unittest.main()
