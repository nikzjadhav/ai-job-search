"""The DuckDB-backed UI server must persist application references safely."""

import csv
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from ui import server

HAS_DUCKDB = bool(shutil.which("duckdb") or Path.home().joinpath(".local", "bin", "duckdb").is_file())


@unittest.skipUnless(HAS_DUCKDB, "DuckDB CLI is not installed")
class UiServerStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        server.ROOT = self.tmp
        server.DB_PATH = self.tmp / "test.duckdb"
        server.JOBS_JSON = self.tmp / "seen_jobs.json"
        server.TRACKER = self.tmp / "tracker.csv"
        server.DASHBOARD = self.tmp / "dashboard.html"
        server.XELATEX = ""
        server.TEX_BIN = ""

        self.JOBS_JSON_data = {
            "seen": {
                "acme_senior-data-engineer": {
                    "title": "Senior Data Engineer",
                    "company": "Acme & Co",
                    "url": "https://example.com/jobs/1",
                    "location": "Kuala Lumpur, Malaysia",
                    "posted_date": "2026-11-01",
                    "first_seen": "2026-11-01",
                    "deadline": "2026-11-20",
                    "fit": "high",
                    "status": "new",
                    "portal": "freehire-search",
                }
            }
        }
        server.JOBS_JSON.write_text(json.dumps(self.JOBS_JSON_data), encoding="utf-8")
        with server.TRACKER.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(server.STANDARD_HEADER)

        server.init_db()
        server.import_jobs()
        server.import_tracker()

    def test_mark_applied_writes_duckdb_and_tracker(self):
        server.mark_applied(
            {
                "job_id": "acme_senior-data-engineer",
                "status": "applied",
                "applied_date": "2026-11-03",
                "notes": "recruiter: Jane, referral",
                "cv_file": "cv/acme.tex",
                "cover_letter_file": "cover_letters/acme.tex",
            }
        )
        state = server.get_state()
        self.assertEqual(len(state["applications"]), 1)
        application = state["applications"][0]
        self.assertEqual(application["company"], "Acme & Co")
        self.assertEqual(application["status"], "applied")
        self.assertTrue(state["jobs"][0]["applied"])

        with server.TRACKER.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["company"], "Acme & Co")
        self.assertEqual(rows[0]["status"], "applied")
        self.assertEqual(rows[0]["cv_file"], "cv/acme.tex")
        # Commas in notes are sanitized because the tracker uses a naive split in readers.
        self.assertNotIn(",", rows[0]["notes"])

        # Re-importing the tracker must update the same application, not duplicate it.
        server.import_tracker()
        self.assertEqual(len(server.get_state()["applications"]), 1)

    def test_cover_letter_text_saves_to_application_folder(self):
        text = "Dear Hiring Manager,\n\nI am applying for the Senior Data Engineer role.\n\nSincerely,\nTest Candidate"
        result = server.mark_applied(
            {
                "job_id": "acme_senior-data-engineer",
                "status": "applied",
                "applied_date": "2026-11-03",
                "notes": "cover letter test",
                "cv_file": "",
                "cover_letter_text": text,
            }
        )
        relative = result["cover_letter_file"]
        self.assertEqual(relative, "cover_letters/cover_acme_co_senior_data_engineer.tex")
        tex_path = self.tmp / relative
        self.assertTrue(tex_path.is_file())
        self.assertIn("Senior Data Engineer", tex_path.read_text(encoding="utf-8"))
        folder_text = self.tmp / "documents/applications/acme_co_senior_data_engineer/cover_letter.txt"
        self.assertTrue(folder_text.is_file())
        self.assertIn("Senior Data Engineer role", folder_text.read_text(encoding="utf-8"))
        state = server.get_state()
        self.assertEqual(state["applications"][0]["cover_letter_text"], text)
        with server.TRACKER.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(rows[0]["cover_letter_file"], relative)

    def test_drafted_cover_letter_does_not_mark_job_applied(self):
        result = server.mark_applied(
            {
                "job_id": "acme_senior-data-engineer",
                "status": "drafted",
                "applied_date": "2026-11-03",
                "notes": "draft only",
                "cv_file": "",
                "cover_letter_text": "Draft cover letter",
            }
        )
        self.assertTrue(result["cover_letter_file"].endswith(".tex"))
        state = server.get_state()
        self.assertEqual(state["applications"][0]["status"], "drafted")
        self.assertFalse(state["jobs"][0]["applied"])

    def test_draft_cover_letter_mentions_job_and_company(self):
        job = server.get_job("acme_senior-data-engineer")
        draft = server.draft_cover_letter(job)
        self.assertIn("Senior Data Engineer", draft)
        self.assertIn("Acme & Co", draft)
        self.assertIn("[YOUR NAME]", draft)

    def test_application_status_update_writes_tracker(self):
        server.mark_applied(
            {
                "job_id": "acme_senior-data-engineer",
                "status": "applied",
                "applied_date": "2026-11-03",
                "notes": "applied",
                "cv_file": "",
                "cover_letter_file": "",
            }
        )
        application = server.get_state()["applications"][0]
        server.update_application({"id": application["id"], "status": "interview", "notes": "first round"})
        state = server.get_state()
        self.assertEqual(state["applications"][0]["status"], "interview")
        self.assertEqual(state["jobs"][0]["application_status"], "interview")
        with server.TRACKER.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(rows[0]["status"], "interview")

    def test_ai_generate_writes_tex_and_drafts_application(self):
        with patch.object(server, "call_deepseek_cover_letter", return_value="First paragraph.\n\nSecond paragraph."), patch.object(
            server, "compile_cover_letter", return_value=("", "xelatex not tested")
        ):
            result = server.generate_cover_letter("acme_senior-data-engineer", description="test posting")
        self.assertTrue(result["tex_file"].endswith(".tex"))
        self.assertEqual(result["pdf_file"], "")
        tex_path = self.tmp / result["tex_file"]
        self.assertTrue(tex_path.is_file())
        tex = tex_path.read_text(encoding="utf-8")
        self.assertIn("[YOUR NAME]", tex)
        self.assertIn("Senior Data Engineer", tex)
        state = server.get_state()
        app = state["applications"][0]
        self.assertEqual(app["status"], "drafted")
        self.assertEqual(app["cover_letter_text"], "First paragraph.\n\nSecond paragraph.")
        self.assertFalse(state["jobs"][0]["applied"])

    def test_build_cover_tex_escapes_special_characters(self):
        tex = server.build_cover_tex("Acme & Co", "Data_Engineer", "Body with 50% and $ sign.")
        self.assertIn(r"Acme \& Co", tex)
        self.assertIn(r"Data\_Engineer", tex)
        self.assertIn(r"50\%", tex)

    def test_clean_cover_text_removes_salutation_and_signature(self):
        raw = "Dear Hiring Manager,\n\nBody paragraph.\n\nSincerely,\nTest Candidate"
        self.assertEqual(server.clean_cover_text(raw), "Body paragraph.")

    def test_country_and_fit_helpers(self):
        self.assertEqual(server.country_for("Kuala Lumpur, Malaysia"), "Malaysia")
        self.assertEqual(server.country_for("Dubai, United Arab Emirates"), "UAE")
        self.assertEqual(server.country_for("Berlin, Germany"), "Germany")
        self.assertEqual(server.country_for("Manama, Bahrain"), "Other")
        self.assertEqual(server.fit_for_job("Senior Data Engineer", "Acme", "Singapore"), "high")
        self.assertEqual(server.fit_for_job("Junior Data Engineer", "Acme", "Singapore"), "low")


if __name__ == "__main__":
    unittest.main()
