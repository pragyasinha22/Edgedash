"""Load and validate project configuration from config.yaml at the repo root."""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field

import yaml  # PyYAML — saves real work: handles types, multiline, comments


_CONFIG_PATH = pathlib.Path("config.yaml")


@dataclass
class Config:
    target_role: str
    target_city: str
    target_seniority: str
    prefer_remote: bool
    keywords: list[str]
    my_skills: list[str]
    experience_years: int
    db_path: str
    min_fit_score: int
    score_batch_size: int
    sources: list[str]
    use_mock_fetcher: bool
    weight_skills: float
    weight_seniority: float
    weight_remote: float
    weight_recency: float
    llm_provider: str
    llm_model: str
    llm_batch_size: int
    llm_rps: int
    llm_rpm: int
    skill_aliases: dict[str, str]
    fetch_interval_hours: int
    fetch_max_pages: int
    fetch_max_listings: int
    score_max_seconds: int
    analyse_max_seconds: int


_DEFAULTS: dict = {
    "target_role": "Software Engineer",
    "target_city": "Remote",
    "target_seniority": "mid",
    "prefer_remote": False,
    "keywords": [],
    "my_skills": [],
    "experience_years": 0,
    "db_path": "edgedash.db",
    "min_fit_score": 50,
    "score_batch_size": 25,
    "sources": ["arbeitnow"],
    "use_mock_fetcher": False,
    "weight_skills": 0.50,
    "weight_seniority": 0.20,
    "weight_remote": 0.15,
    "weight_recency": 0.15,
    "llm_provider": "gemini",
    "llm_model": "gemini-2.5-flash",
    "llm_batch_size": 25,
    "llm_rps": 1,
    "llm_rpm": 15,
    "skill_aliases": {},
    "fetch_interval_hours": 6,
    "fetch_max_pages": 5,
    "fetch_max_listings": 100,
    "score_max_seconds": 300,
    "analyse_max_seconds": 180,
}


def load_config(path: pathlib.Path = _CONFIG_PATH) -> Config:
    if not path.exists():
        raise FileNotFoundError(
            f"config.yaml not found at '{path.resolve()}'. "
            "Create one from the example in the repo root before running EdgeDash."
        )

    with path.open("r", encoding="utf-8") as fh:
        raw: dict = yaml.safe_load(fh) or {}

    merged = {**_DEFAULTS, **raw}

    # skill_aliases: keys and values are both lowercased for consistent lookup
    raw_aliases: dict = merged.get("skill_aliases") or {}
    skill_aliases = {str(k).lower(): str(v).lower() for k, v in raw_aliases.items()}

    return Config(
        target_role=str(merged["target_role"]),
        target_city=str(merged["target_city"]),
        target_seniority=str(merged["target_seniority"]),
        prefer_remote=bool(merged["prefer_remote"]),
        keywords=list(merged["keywords"]),
        my_skills=list(merged["my_skills"]),
        experience_years=int(merged["experience_years"]),
        db_path=str(merged["db_path"]),
        min_fit_score=int(merged["min_fit_score"]),
        score_batch_size=int(merged["score_batch_size"]),
        sources=list(merged["sources"]),
        use_mock_fetcher=bool(merged["use_mock_fetcher"]),
        weight_skills=float(merged["weight_skills"]),
        weight_seniority=float(merged["weight_seniority"]),
        weight_remote=float(merged["weight_remote"]),
        weight_recency=float(merged["weight_recency"]),
        llm_provider=str(merged["llm_provider"]),
        llm_model=str(merged["llm_model"]),
        llm_batch_size=int(merged["llm_batch_size"]),
        llm_rps=int(merged["llm_rps"]),
        llm_rpm=int(merged["llm_rpm"]),
        skill_aliases=skill_aliases,
        fetch_interval_hours=int(merged["fetch_interval_hours"]),
        fetch_max_pages=int(merged["fetch_max_pages"]),
        fetch_max_listings=int(merged["fetch_max_listings"]),
        score_max_seconds=int(merged["score_max_seconds"]),
        analyse_max_seconds=int(merged["analyse_max_seconds"]),
    )
