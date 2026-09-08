"""Tests for planning.py — deterministic, no I/O."""

from datetime import datetime, timezone, timedelta

from edgedash.config import Config
from edgedash.planning import Plan, build_plan
from edgedash.state import SystemState


def test_all_stale():
    """All three agents should run when state is fully stale."""
    state = SystemState(
        last_fetch_at="2024-01-01T00:00:00+00:00",
        hours_since_fetch=10.0,  # >= default 6
        unscored_count=5,
        gaps_computed_at="2024-01-01T00:00:00+00:00",
        gaps_stale=True,
        last_cycle_verdict="ok",
        last_cycle_at="2024-01-01T01:00:00+00:00",
    )
    config = Config(
        target_role="Data Analyst",
        target_city="Bengaluru",
        target_seniority="mid",
        prefer_remote=False,
        keywords=["data"],
        my_skills=["Python"],
        experience_years=2,
        db_path="test.db",
        min_fit_score=60,
        score_batch_size=25,
        sources=["arbeitnow"],
        use_mock_fetcher=False,
        weight_skills=0.5,
        weight_seniority=0.2,
        weight_remote=0.15,
        weight_recency=0.15,
        llm_provider="gemini",
        llm_model="gemini-2.5-flash",
        llm_batch_size=25,
        llm_rps=1,
        llm_rpm=15,
        skill_aliases={},
        fetch_interval_hours=6,
        fetch_max_pages=5,
        fetch_max_listings=100,
        score_max_seconds=300,
        analyse_max_seconds=180,
    )
    plan = build_plan(state, config)
    assert len(plan.tasks) == 3
    assert all(not task.skipped for task in plan.tasks)
    assert plan.tasks[0].agent_name == "fetcher"
    assert plan.tasks[1].agent_name == "scorer"
    assert plan.tasks[2].agent_name == "gap_analyzer"


def test_nothing_to_do():
    """All three agents should be skipped when state is fresh."""
    state = SystemState(
        last_fetch_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        hours_since_fetch=2.0,  # < default 6
        unscored_count=0,
        gaps_computed_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        gaps_stale=False,
        last_cycle_verdict="ok",
        last_cycle_at=datetime.now(timezone.utc).isoformat(),
    )
    config = Config(
        target_role="Data Analyst",
        target_city="Bengaluru",
        target_seniority="mid",
        prefer_remote=False,
        keywords=["data"],
        my_skills=["Python"],
        experience_years=2,
        db_path="test.db",
        min_fit_score=60,
        score_batch_size=25,
        sources=["arbeitnow"],
        use_mock_fetcher=False,
        weight_skills=0.5,
        weight_seniority=0.2,
        weight_remote=0.15,
        weight_recency=0.15,
        llm_provider="gemini",
        llm_model="gemini-2.5-flash",
        llm_batch_size=25,
        llm_rps=1,
        llm_rpm=15,
        skill_aliases={},
        fetch_interval_hours=6,
        fetch_max_pages=5,
        fetch_max_listings=100,
        score_max_seconds=300,
        analyse_max_seconds=180,
    )
    plan = build_plan(state, config)
    assert len(plan.tasks) == 3
    assert all(task.skipped for task in plan.tasks)
    assert "skipped" in plan.tasks[0].reason
    assert "skipped" in plan.tasks[1].reason
    assert "skipped" in plan.tasks[2].reason


def test_only_unscored():
    """Only scorer should run when there are unscored listings but fetch is recent and gaps are fresh."""
    state = SystemState(
        last_fetch_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        hours_since_fetch=2.0,  # < default 6
        unscored_count=10,
        gaps_computed_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        gaps_stale=False,
        last_cycle_verdict="ok",
        last_cycle_at=datetime.now(timezone.utc).isoformat(),
    )
    config = Config(
        target_role="Data Analyst",
        target_city="Bengaluru",
        target_seniority="mid",
        prefer_remote=False,
        keywords=["data"],
        my_skills=["Python"],
        experience_years=2,
        db_path="test.db",
        min_fit_score=60,
        score_batch_size=25,
        sources=["arbeitnow"],
        use_mock_fetcher=False,
        weight_skills=0.5,
        weight_seniority=0.2,
        weight_remote=0.15,
        weight_recency=0.15,
        llm_provider="gemini",
        llm_model="gemini-2.5-flash",
        llm_batch_size=25,
        llm_rps=1,
        llm_rpm=15,
        skill_aliases={},
        fetch_interval_hours=6,
        fetch_max_pages=5,
        fetch_max_listings=100,
        score_max_seconds=300,
        analyse_max_seconds=180,
    )
    plan = build_plan(state, config)
    assert len(plan.tasks) == 3
    assert plan.tasks[0].skipped  # fetcher skipped
    assert not plan.tasks[1].skipped  # scorer runs
    assert plan.tasks[2].skipped  # gap_analyzer skipped


def test_gaps_stale_only():
    """Only gap_analyzer should run when gaps are stale but fetch is recent and nothing unscored."""
    state = SystemState(
        last_fetch_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        hours_since_fetch=2.0,  # < default 6
        unscored_count=0,
        gaps_computed_at=(datetime.now(timezone.utc) - timedelta(hours=10)).isoformat(),
        gaps_stale=True,  # stale because a newer score exists
        last_cycle_verdict="ok",
        last_cycle_at=datetime.now(timezone.utc).isoformat(),
    )
    config = Config(
        target_role="Data Analyst",
        target_city="Bengaluru",
        target_seniority="mid",
        prefer_remote=False,
        keywords=["data"],
        my_skills=["Python"],
        experience_years=2,
        db_path="test.db",
        min_fit_score=60,
        score_batch_size=25,
        sources=["arbeitnow"],
        use_mock_fetcher=False,
        weight_skills=0.5,
        weight_seniority=0.2,
        weight_remote=0.15,
        weight_recency=0.15,
        llm_provider="gemini",
        llm_model="gemini-2.5-flash",
        llm_batch_size=25,
        llm_rps=1,
        llm_rpm=15,
        skill_aliases={},
        fetch_interval_hours=6,
        fetch_max_pages=5,
        fetch_max_listings=100,
        score_max_seconds=300,
        analyse_max_seconds=180,
    )
    plan = build_plan(state, config)
    assert len(plan.tasks) == 3
    assert plan.tasks[0].skipped  # fetcher skipped
    assert plan.tasks[1].skipped  # scorer skipped
    assert not plan.tasks[2].skipped  # gap_analyzer runs


def test_plan_render():
    """Plan.render() should produce readable output."""
    state = SystemState(
        last_fetch_at=None,
        hours_since_fetch=None,
        unscored_count=0,
        gaps_computed_at=None,
        gaps_stale=True,
        last_cycle_verdict=None,
        last_cycle_at=None,
    )
    config = Config(
        target_role="Data Analyst",
        target_city="Bengaluru",
        target_seniority="mid",
        prefer_remote=False,
        keywords=["data"],
        my_skills=["Python"],
        experience_years=2,
        db_path="test.db",
        min_fit_score=60,
        score_batch_size=25,
        sources=["arbeitnow"],
        use_mock_fetcher=False,
        weight_skills=0.5,
        weight_seniority=0.2,
        weight_remote=0.15,
        weight_recency=0.15,
        llm_provider="gemini",
        llm_model="gemini-2.5-flash",
        llm_batch_size=25,
        llm_rps=1,
        llm_rpm=15,
        skill_aliases={},
        fetch_interval_hours=6,
        fetch_max_pages=5,
        fetch_max_listings=100,
        score_max_seconds=300,
        analyse_max_seconds=180,
    )
    plan = build_plan(state, config)
    rendered = plan.render()
    assert "fetcher" in rendered
    assert "scorer" in rendered
    assert "gap_analyzer" in rendered
    assert "[RUN]" in rendered or "[SKIP]" in rendered
