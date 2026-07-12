from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20260711_0001_product_reliability_schema.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("loopie_v2_migration", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_json_seed_literals_survive_sqlalchemy_offline_compilation() -> None:
    migration = _load_migration()
    value = {"count": 12, "enabled": False, "nested": {"version": 1}}
    statement = sa.text(f"SELECT {migration._json(value)}")
    compiled = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    encoded = compiled.removeprefix("SELECT '").removesuffix("'::jsonb")
    assert json.loads(encoded) == value
    assert "NULL" not in encoded


def test_fixture_seed_keeps_golden_labels_out_of_ticket_metadata() -> None:
    migration = _load_migration()
    fixtures = [
        json.loads(line)
        for line in (MIGRATION.parents[1] / "seeds" / "v2_tickets.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    assert len(fixtures) == 17
    for fixture in fixtures:
        assert fixture["expected_action"]
        metadata = migration._ticket_metadata(fixture)
        assert not migration.GOLDEN_FIELDS.intersection(metadata)
        assert "case_id" not in metadata
        assert "request" not in metadata
