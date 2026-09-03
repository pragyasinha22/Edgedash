"""
Focused tests for location filtering, role filtering, Scorer, and GapAnalyzer.
Run with: python -m pytest tests/ -v
      or: python -m unittest discover tests
"""

from __future__ import annotations

import sys
import os
import tempfile
import unittest

# Ensure project root is on the path when running directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from edgedash.sources.arbeitnow import _location_tier, _matches_role, _normalise_location
from edgedash.agents.scorer import _compute_score
from edgedash.agents.gap_analyzer import _extract_skills, SKILLS_VOCAB
from edgedash import storage


# ---------------------------------------------------------------------------
# Helper to make a fake job dict
# ---------------------------------------------------------------------------

def _job(title: str, location: str, description: str = "") -> dict:
    return {
        "title": title,
        "location": location,
        "description": description,
        "slug": "test-slug",
        "company_name": "TestCo",
        "url": "https://example.com/job",
        "created_at": "2026-09-01",
    }


# ---------------------------------------------------------------------------
# Location normalisation & tier tests
# ---------------------------------------------------------------------------

class TestLocationNormalisation(unittest.TestCase):

    def test_bengaluru_accepted(self) -> None:
        job = _job("Data Analyst", "Bengaluru")
        self.assertEqual(_location_tier(job), 3, "Bengaluru should be tier 3")

    def test_bangalore_accepted(self) -> None:
        job = _job("Data Analyst", "Bangalore, Karnataka")
        self.assertEqual(_location_tier(job), 3, "Bangalore should normalise to Bengaluru (tier 3)")

    def test_bangalore_comma_variant(self) -> None:
        job = _job("Data Analyst", "Bangalore, India")
        self.assertEqual(_location_tier(job), 3)

    def test_remote_accepted(self) -> None:
        job = _job("Data Analyst", "Remote")
        self.assertEqual(_location_tier(job), 2, "Remote should be tier 2")

    def test_hybrid_accepted(self) -> None:
        job = _job("Data Analyst", "Hybrid – Bengaluru / Remote")
        # Contains "bengaluru" → tier 3
        self.assertEqual(_location_tier(job), 3)

    def test_remote_india_accepted(self) -> None:
        job = _job("Data Analyst", "Remote, India")
        self.assertEqual(_location_tier(job), 2, "Remote India should be tier 2 (remote wins)")

    def test_india_only_accepted(self) -> None:
        job = _job("Data Analyst", "India")
        self.assertEqual(_location_tier(job), 1, "India-only should be tier 1")

    def test_berlin_rejected(self) -> None:
        job = _job("Data Analyst", "Berlin, Germany")
        self.assertEqual(_location_tier(job), 0, "Berlin should be tier 0 (rejected)")

    def test_munich_rejected(self) -> None:
        job = _job("Data Analyst", "Munich")
        self.assertEqual(_location_tier(job), 0, "Munich should be tier 0 (rejected)")

    def test_london_rejected(self) -> None:
        job = _job("Data Analyst", "London, UK")
        self.assertEqual(_location_tier(job), 0, "London should be tier 0 (rejected)")

    def test_new_york_rejected(self) -> None:
        job = _job("Data Analyst", "New York, NY")
        self.assertEqual(_location_tier(job), 0, "New York should be tier 0 (rejected)")

    def test_normalise_bangalore_to_bengaluru(self) -> None:
        result = _normalise_location("Bangalore, Karnataka")
        self.assertIn("bengaluru", result)
        self.assertNotIn("bangalore", result)


# ---------------------------------------------------------------------------
# Role / keyword filter tests
# ---------------------------------------------------------------------------

class TestRoleFiltering(unittest.TestCase):

    def test_data_analyst_title_accepted(self) -> None:
        job = _job("Data Analyst", "Bengaluru", "SQL and Python required")
        self.assertTrue(_matches_role(job), "Data Analyst title should match")

    def test_senior_data_analyst_accepted(self) -> None:
        job = _job("Senior Data Analyst – Risk", "Bengaluru", "Power BI, SQL")
        self.assertTrue(_matches_role(job))

    def test_business_analyst_accepted(self) -> None:
        job = _job("Business Analyst", "Bengaluru", "excel required")
        self.assertTrue(_matches_role(job))

    def test_reporting_analyst_accepted(self) -> None:
        job = _job("Reporting Analyst", "Remote", "Tableau and SQL")
        self.assertTrue(_matches_role(job))

    def test_frontend_engineer_rejected(self) -> None:
        # Python in description should NOT qualify a Frontend Engineer
        job = _job(
            "Frontend Engineer",
            "Bengaluru",
            "React, JavaScript, Python scripting. SQL knowledge a plus."
        )
        self.assertFalse(
            _matches_role(job),
            "Frontend Engineer should be rejected even if description mentions Python/SQL"
        )

    def test_devops_engineer_rejected(self) -> None:
        job = _job(
            "DevOps Engineer",
            "Bengaluru",
            "Python, SQL, AWS, Kubernetes, CI/CD pipelines"
        )
        self.assertFalse(_matches_role(job), "DevOps Engineer should be rejected")

    def test_ml_engineer_rejected(self) -> None:
        job = _job(
            "Machine Learning Engineer",
            "Bengaluru",
            "Python, SQL, scikit-learn, TensorFlow, pandas"
        )
        self.assertFalse(_matches_role(job), "ML Engineer should be rejected")

    def test_role_signal_in_description_with_tech_accepted(self) -> None:
        # No role in title, but description has "data analyst" + SQL
        job = _job(
            "Analyst",
            "Bengaluru",
            "We are hiring for a data analyst role. SQL and Python required."
        )
        self.assertTrue(_matches_role(job))

    def test_role_signal_only_no_tech_title_qualifies(self) -> None:
        # Title alone with a strong role signal qualifies without tech keywords
        job = _job("Data Analyst", "Remote", "Good communication skills required")
        self.assertTrue(_matches_role(job))


# ---------------------------------------------------------------------------
# Scorer tests
# ---------------------------------------------------------------------------

class _FakeConfig:
    """Minimal config substitute for scorer tests."""
    def __init__(self, my_skills: list[str], experience_years: int) -> None:
        self.my_skills = my_skills
        self.experience_years = experience_years


class TestScorer(unittest.TestCase):

    def test_score_in_range(self) -> None:
        config = _FakeConfig(["SQL", "Python", "Excel"], 2)
        listing = {
            "id": "test1",
            "title": "Data Analyst",
            "location": "Bengaluru",
            "description": "SQL, Python, Excel required. 2 years experience.",
        }
        score, reason = _compute_score(listing, config)
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 100)

    def test_perfect_match_scores_high(self) -> None:
        config = _FakeConfig(["SQL", "Python", "Excel", "Pandas", "Tableau"], 2)
        listing = {
            "id": "test2",
            "title": "Data Analyst",
            "location": "Bengaluru",
            "description": "SQL, Python, Excel, Pandas, Tableau required. 2 years experience.",
        }
        score, reason = _compute_score(listing, config)
        self.assertGreaterEqual(score, 70, f"Perfect match should score high, got {score}")

    def test_wrong_role_scores_low(self) -> None:
        config = _FakeConfig(["SQL", "Python"], 2)
        listing = {
            "id": "test3",
            "title": "Backend Engineer",
            "location": "Berlin",
            "description": "Java, Spring Boot, Docker",
        }
        score, reason = _compute_score(listing, config)
        self.assertLessEqual(score, 20, f"Backend Engineer in Berlin should score low, got {score}")

    def test_deterministic(self) -> None:
        config = _FakeConfig(["SQL", "Python"], 2)
        listing = {
            "id": "test4",
            "title": "Data Analyst",
            "location": "Bengaluru",
            "description": "SQL and Python required",
        }
        score1, reason1 = _compute_score(listing, config)
        score2, reason2 = _compute_score(listing, config)
        self.assertEqual(score1, score2, "Scorer must be deterministic")
        self.assertEqual(reason1, reason2)

    def test_reason_is_human_readable(self) -> None:
        config = _FakeConfig(["SQL"], 1)
        listing = {
            "id": "test5",
            "title": "Data Analyst",
            "location": "Remote",
            "description": "SQL experience required",
        }
        _, reason = _compute_score(listing, config)
        self.assertIn("role=", reason)
        self.assertIn("location=", reason)
        self.assertIn("skills=", reason)
        self.assertIn("experience=", reason)

    def test_bengaluru_location_scores_full(self) -> None:
        config = _FakeConfig([], 0)
        listing = {
            "id": "test6",
            "title": "Data Analyst",
            "location": "Bengaluru",
            "description": "",
        }
        score, reason = _compute_score(listing, config)
        self.assertIn("20", reason, "Bengaluru should contribute 20 location points")


# ---------------------------------------------------------------------------
# GapAnalyzer tests
# ---------------------------------------------------------------------------

class TestGapAnalyzer(unittest.TestCase):

    def test_power_bi_identified_as_gap(self) -> None:
        # my_skills does NOT include power bi
        my_skills = {"sql", "python", "excel"}
        description = "Power BI, Tableau, SQL required"
        found = _extract_skills(description)
        gaps = found - my_skills
        self.assertIn("power bi", gaps, "Power BI should be identified as a gap")

    def test_tableau_identified_as_gap(self) -> None:
        my_skills = {"sql", "python", "excel"}
        description = "Tableau, SQL required. Python a bonus."
        found = _extract_skills(description)
        gaps = found - my_skills
        self.assertIn("tableau", gaps, "Tableau should be identified as a gap")

    def test_known_skill_not_a_gap(self) -> None:
        my_skills = {"sql", "python", "excel"}
        description = "SQL and Python required"
        found = _extract_skills(description)
        gaps = found - my_skills
        self.assertNotIn("sql", gaps)
        self.assertNotIn("python", gaps)

    def test_extract_skills_case_insensitive(self) -> None:
        description = "Proficiency in POWER BI and TABLEAU is required."
        found = _extract_skills(description)
        self.assertIn("power bi", found)
        self.assertIn("tableau", found)

    def test_extract_skills_vocab_coverage(self) -> None:
        # Ensure at least some key skills are in vocab
        for skill in ["sql", "python", "tableau", "power bi", "dbt", "spark"]:
            self.assertIn(skill, SKILLS_VOCAB, f"'{skill}' should be in SKILLS_VOCAB")

    def test_gap_analyzer_storage_integration(self) -> None:
        """Integration test: upsert gaps and read them back."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            storage.init_db(db_path)
            gaps = [
                {"skill": "power bi", "frequency": 3, "last_seen": "2026-09-01"},
                {"skill": "tableau", "frequency": 2, "last_seen": "2026-09-01"},
            ]
            storage.upsert_skill_gaps(db_path, gaps)

            # Second upsert — frequency should accumulate
            storage.upsert_skill_gaps(db_path, [
                {"skill": "power bi", "frequency": 1, "last_seen": "2026-09-02"},
            ])

            import sqlite3
            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT frequency FROM skill_gaps WHERE skill = 'power bi'"
            ).fetchone()
            conn.close()

            self.assertIsNotNone(row)
            self.assertEqual(row[0], 4, "Power BI frequency should accumulate to 4")
        finally:
            os.unlink(db_path)


if __name__ == "__main__":
    unittest.main()
