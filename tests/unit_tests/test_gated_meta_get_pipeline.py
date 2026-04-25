"""End-to-end and apply_changeset-chain tests for gated config.get -> meta_get (provenance from move-to-meta)."""

from pathlib import Path

import pytest

from dbt_autofix.fields_properties_configs import models_allowed_config
from dbt_autofix.refactor import process_python_files, process_sql_files
from dbt_autofix.refactors.changesets.dbt_python import (
    move_custom_config_access_to_meta_python,
    refactor_custom_configs_to_meta_python,
)
from dbt_autofix.refactors.changesets.dbt_sql import (
    refactor_custom_configs_to_meta_sql,
    remove_unmatched_endings,
)
from dbt_autofix.refactors.changesets.dbt_sql_improved import move_custom_config_access_to_meta_sql_improved
from dbt_autofix.refactors.results import (
    PythonRefactorConfig,
    PythonRefactorResult,
    SQLRefactorConfig,
    SQLRefactorResult,
)
from dbt_autofix.retrieve_schemas import SchemaSpecs


class FakeSchemaSpecs(SchemaSpecs):
    """Test double: refactor rules only read ``yaml_specs_per_node_type``; no network."""

    def __init__(self) -> None:
        self.yaml_specs_per_node_type = {
            "models": models_allowed_config,
        }


@pytest.fixture
def sql_cfg() -> SQLRefactorConfig:
    return SQLRefactorConfig(schema_specs=FakeSchemaSpecs(), node_type="models")


@pytest.fixture
def py_cfg() -> PythonRefactorConfig:
    return PythonRefactorConfig(schema_specs=FakeSchemaSpecs(), node_type="models")


def test_sql_apply_changeset_chain_merges_keys_moved_to_meta_for_meta_get(
    sql_cfg: SQLRefactorConfig,
) -> None:
    """After refactor_custom_configs_to_meta_sql, keys merge; access rule rewrites get only for those keys."""
    sql = """{{ config(
    materialized='table',
    moved_only='1',
) }}

SELECT
  '{{ config.get('moved_only') }}' AS a,
  '{{ config.get('not_in_meta_this_run') }}' AS b
"""
    r = SQLRefactorResult(
        dry_run=True,
        file_path=Path("m.sql"),
        refactored_file_path=Path("m.sql"),
        refactored_content=sql,
        original_content=sql,
        refactors=[],
    )
    for fn in (
        remove_unmatched_endings,
        refactor_custom_configs_to_meta_sql,
        move_custom_config_access_to_meta_sql_improved,
    ):
        r.apply_changeset(fn, sql_cfg)

    assert "moved_only" in r.keys_moved_to_meta
    assert "config.meta_get('moved_only')" in r.refactored_content
    assert "config.get('not_in_meta_this_run')" in r.refactored_content
    move_result = r.refactors[-1]
    assert any("not_in_meta_this_run" in w and "not moved to meta" in w for w in move_result.refactor_warnings)


def test_python_apply_changeset_chain_merges_keys_moved_to_meta_for_meta_get(
    py_cfg: PythonRefactorConfig,
) -> None:
    """Python: same pipeline merges keys; meta_get only for keys placed under meta in the first rule."""
    code = """def model(dbt, session):
    dbt.config(materialized="table", in_meta="v")
    a = dbt.config.get("in_meta")
    b = dbt.config.get("not_moved")
    return session.sql("select 1")
"""
    r = PythonRefactorResult(
        dry_run=True,
        file_path=Path("m.py"),
        refactored_file_path=Path("m.py"),
        refactored_content=code,
        original_content=code,
        refactors=[],
    )
    for fn in (refactor_custom_configs_to_meta_python, move_custom_config_access_to_meta_python):
        r.apply_changeset(fn, py_cfg)

    assert "in_meta" in r.keys_moved_to_meta
    out = r.refactored_content
    assert "dbt.config.meta_get" in out and "in_meta" in out
    assert 'dbt.config.get("not_moved")' in out
    move_result = r.refactors[-1]
    assert any("not_moved" in w and "not moved to meta" in w for w in move_result.refactor_warnings)


def test_process_sql_files_propagates_keys_for_gated_get(tmp_path: Path, sql_cfg: SQLRefactorConfig) -> None:
    """process_sql_files order matches production; gating does not need hand-built frozensets in callers."""
    (tmp_path / "dbt_project.yml").write_text('name: t\nversion: "1.0.0"\nconfig-version: 2\nmodel-paths: ["models"]\n')
    models = tmp_path / "models"
    models.mkdir()
    (models / "gated.sql").write_text(
        """{{ config(
    materialized='table',
    from_config='v'
) }}
SELECT '{{ config.get('from_config') }}' AS g
""",
        encoding="utf-8",
    )
    results = process_sql_files(tmp_path, {"models": "models"}, sql_cfg.schema_specs)
    assert len(results) == 1
    r = results[0]
    assert "from_config" in r.keys_moved_to_meta
    assert "config.meta_get('from_config')" in r.refactored_content


def test_process_python_files_propagates_keys_for_gated_get(tmp_path: Path, py_cfg: PythonRefactorConfig) -> None:
    """process_python_files runs config-to-meta then get; merged keys_moved_to_meta drive meta_get rewrites."""
    (tmp_path / "dbt_project.yml").write_text('name: t\nversion: "1.0.0"\nconfig-version: 2\nmodel-paths: ["models"]\n')
    models = tmp_path / "models"
    models.mkdir()
    (models / "gated.py").write_text(
        """def model(dbt, session):
    dbt.config(materialized="table", p_flag="1")
    x = dbt.config.get("p_flag")
    return session.sql("select 1")
""",
        encoding="utf-8",
    )
    results = process_python_files(tmp_path, {"models": "models"}, py_cfg.schema_specs)
    assert len(results) == 1
    r = results[0]
    assert "p_flag" in r.keys_moved_to_meta
    assert 'dbt.config.meta_get("p_flag")' in r.refactored_content


def test_process_sql_preexisting_config_meta_still_allows_get_to_meta_get(
    tmp_path: Path, sql_cfg: SQLRefactorConfig
) -> None:
    """When nothing is moved this run but ``meta=`` already lists keys, gate get -> meta_get on those keys.

    Covers the case a prior run already moved into meta while get -> meta_get failed or was skipped.
    """
    (tmp_path / "dbt_project.yml").write_text('name: t\nversion: "1.0.0"\nconfig-version: 2\nmodel-paths: ["models"]\n')
    models = tmp_path / "models"
    models.mkdir()
    (models / "only_meta.sql").write_text(
        """{{ config(
  materialized='view',
  meta={'only_in_meta': '1'}
) }}

select '{{ config.get('only_in_meta') }}' as x
""",
        encoding="utf-8",
    )
    results = process_sql_files(tmp_path, {"models": "models"}, sql_cfg.schema_specs)
    assert len(results) == 1
    r = results[0]
    assert "only_in_meta" in r.keys_moved_to_meta
    assert "config.meta_get('only_in_meta')" in r.refactored_content


def test_process_python_preexisting_meta_still_allows_get_to_meta_get(
    tmp_path: Path, py_cfg: PythonRefactorConfig
) -> None:
    """dbt.config with only meta= (no top-level custom) still contributes keys for gating."""
    (tmp_path / "dbt_project.yml").write_text('name: t\nversion: "1.0.0"\nconfig-version: 2\nmodel-paths: ["models"]\n')
    models = tmp_path / "models"
    models.mkdir()
    (models / "only_meta.py").write_text(
        """def model(dbt, session):
    dbt.config(materialized="view", meta={"only_m": "x"})
    v = dbt.config.get("only_m")
    return session.sql("select 1")
""",
        encoding="utf-8",
    )
    results = process_python_files(tmp_path, {"models": "models"}, py_cfg.schema_specs)
    assert len(results) == 1
    r = results[0]
    assert "only_m" in r.keys_moved_to_meta
    assert "dbt.config.meta_get" in r.refactored_content
