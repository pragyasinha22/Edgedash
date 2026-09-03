"""
MockFetcher — returns 12 realistic fake job listings.
4 of the 12 carry stable, hardcoded IDs so deduplication is provable on run 2.
"""

from __future__ import annotations

from datetime import datetime, timezone

from edgedash import storage
from edgedash.agents.base import AgentResult
from edgedash.config import Config

NAME = "MockFetcher"

# These 4 IDs are stable across every run — dedup targets.
_STABLE_IDS = [
    "stable0000000001",
    "stable0000000002",
    "stable0000000003",
    "stable0000000004",
]


def _build_listings(role: str, city: str) -> list[dict]:
    now = datetime.now(timezone.utc).isoformat()

    # 8 dynamically sourced listings (id derived from source+url at upsert time)
    dynamic = [
        {
            "title": f"Senior {role}",
            "company": "Flipkart",
            "location": city,
            "url": "https://careers.flipkart.com/job/senior-da-blr",
            "description": (
                "Own end-to-end analytics for the supply-chain vertical. "
                "Required: Python, SQL, Tableau, statistics. "
                "Nice to have: dbt, Spark."
            ),
            "source": "mock",
            "posted_at": "2026-07-30",
        },
        {
            "title": f"{role} – Growth",
            "company": "PhonePe",
            "location": city,
            "url": "https://careers.phonepe.com/job/da-growth",
            "description": (
                "Drive growth experimentation and A/B test analysis. "
                "Stack: SQL, Python (Pandas, NumPy), Mixpanel, Power BI."
            ),
            "source": "mock",
            "posted_at": "2026-07-31",
        },
        {
            "title": f"Junior {role}",
            "company": "Swiggy",
            "location": city,
            "url": "https://careers.swiggy.com/job/junior-da",
            "description": (
                "Support ops analytics team. Excel, SQL required. "
                "Python and Tableau a plus. 0-2 years experience."
            ),
            "source": "mock",
            "posted_at": "2026-08-01",
        },
        {
            "title": f"{role} – Risk & Compliance",
            "company": "Razorpay",
            "location": city,
            "url": "https://razorpay.com/jobs/da-risk",
            "description": (
                "Analyse fraud patterns, build monitoring dashboards. "
                "SQL (advanced), Python, Power BI. Fintech domain preferred."
            ),
            "source": "mock",
            "posted_at": "2026-08-02",
        },
        {
            "title": f"Lead {role}",
            "company": "Meesho",
            "location": city,
            "url": "https://meesho.io/careers/lead-da",
            "description": (
                "Lead a team of 3 analysts. Define metrics, own dashboards. "
                "Expert SQL, Python, dbt, Looker. 5+ years required."
            ),
            "source": "mock",
            "posted_at": "2026-08-01",
        },
        {
            "title": f"{role} – Product",
            "company": "Zepto",
            "location": city,
            "url": "https://zepto.com/jobs/product-da",
            "description": (
                "Partner with PMs to instrument features and analyse funnels. "
                "SQL, Python, Amplitude, Metabase."
            ),
            "source": "mock",
            "posted_at": "2026-08-03",
        },
        {
            "title": f"Associate {role}",
            "company": "Ola",
            "location": city,
            "url": "https://ola.com/careers/associate-da",
            "description": (
                "Reporting and ad-hoc analysis for driver-ops. "
                "SQL, Excel, basic Python. Freshers welcome."
            ),
            "source": "mock",
            "posted_at": "2026-07-29",
        },
        {
            "title": f"{role} – Marketing",
            "company": "Myntra",
            "location": city,
            "url": "https://myntra.com/jobs/marketing-da",
            "description": (
                "Campaign performance, attribution modelling. "
                "SQL, Python, Google Analytics, Tableau. 1-3 years."
            ),
            "source": "mock",
            "posted_at": "2026-08-02",
        },
    ]

    # 4 listings with stable IDs — identical on every run to prove dedup
    stable = [
        {
            "id": _STABLE_IDS[0],
            "title": f"{role} – Data Platform",
            "company": "Infosys BPM",
            "location": city,
            "url": "https://infosys.com/jobs/da-platform",
            "description": (
                "Build and maintain internal BI platform. "
                "SQL, Python, Power BI, Azure Data Factory."
            ),
            "source": "mock",
            "posted_at": "2026-07-28",
        },
        {
            "id": _STABLE_IDS[1],
            "title": f"Senior {role} – CX",
            "company": "Amazon India",
            "location": city,
            "url": "https://amazon.jobs/da-cx-blr",
            "description": (
                "Customer experience analytics, defect reduction. "
                "SQL, Python, QuickSight, statistical modelling."
            ),
            "source": "mock",
            "posted_at": "2026-07-27",
        },
        {
            "id": _STABLE_IDS[2],
            "title": f"{role} – Supply Chain",
            "company": "Bigbasket",
            "location": city,
            "url": "https://bigbasket.com/careers/da-sc",
            "description": (
                "Forecast demand, track inventory KPIs. "
                "SQL, Python (Scikit-learn), Tableau, Excel."
            ),
            "source": "mock",
            "posted_at": "2026-07-26",
        },
        {
            "id": _STABLE_IDS[3],
            "title": f"Contract {role}",
            "company": "Accenture",
            "location": city,
            "url": "https://accenture.com/jobs/contract-da-blr",
            "description": (
                "6-month engagement, client-facing reporting. "
                "Advanced Excel, SQL, Power BI. Immediate joiner preferred."
            ),
            "source": "mock",
            "posted_at": "2026-07-25",
        },
    ]

    # Stamp fetched_at on all rows
    for row in dynamic + stable:
        row.setdefault("fetched_at", now)
        row.setdefault("fit_score", None)
        row.setdefault("fit_reason", None)

    return dynamic + stable


class MockFetcher:
    name: str = NAME

    def run(self, config: Config) -> AgentResult:
        from datetime import datetime, timezone
        started = datetime.now(timezone.utc).isoformat()

        listings = _build_listings(config.target_role, config.target_city)
        new_count = storage.upsert_listings(config.db_path, listings)

        notes = (
            f"Generated {len(listings)} listings, {new_count} new. "
            f"{len(listings) - new_count} duplicates skipped."
        )
        storage.log_cycle(
            path=config.db_path,
            agent=NAME,
            started_at=started,
            records_touched=new_count,
            status="ok",
            notes=notes,
        )
        return AgentResult(
            agent=NAME,
            status="ok",
            records_touched=new_count,
            notes=notes,
        )
