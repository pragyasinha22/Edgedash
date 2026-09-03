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
    keywords: list[str]
    my_skills: list[str]
    experience_years: int
    db_path: str
    min_fit_score: int
    sources: list[str]
    use_mock_fetcher: bool


_DEFAULTS: dict = {
    "target_role": "Software Engineer",
    "target_city": "Remote",
    "keywords": [],
    "my_skills": [],
    "experience_years": 0,
    "db_path": "edgedash.db",
    "min_fit_score": 50,
    "sources": ["arbeitnow"],
    "use_mock_fetcher": False,
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

    return Config(
        target_role=str(merged["target_role"]),
        target_city=str(merged["target_city"]),
        keywords=list(merged["keywords"]),
        my_skills=list(merged["my_skills"]),
        experience_years=int(merged["experience_years"]),
        db_path=str(merged["db_path"]),
        min_fit_score=int(merged["min_fit_score"]),
        sources=list(merged["sources"]),
        use_mock_fetcher=bool(merged["use_mock_fetcher"]),
    )
