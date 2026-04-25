"""Tests for schema.yml model config.meta / meta key collection (gating get -> meta_get)."""

from pathlib import Path

from dbt_autofix.schema_yml_model_meta import SchemaYmlModelMetaResolver, collect_model_meta_keys_by_name_from_project


def test_collect_merges_models_path_yaml(tmp_path: Path) -> None:
    models = tmp_path / "models"
    models.mkdir()
    (models / "schema.yml").write_text(
        """version: 2
models:
  - name: f
    config:
      meta:
        k1: 1
        k2: "x"
  - name: f
    meta:
      k3: true
""",
        encoding="utf-8",
    )
    m = collect_model_meta_keys_by_name_from_project(
        tmp_path,
        {"models": "models"},
    )
    assert m["f"] == frozenset({"k1", "k2", "k3"})


def test_skips_paths_that_are_not_models_node_type(tmp_path: Path) -> None:
    """Only ``node_type == 'models'`` rel paths are scanned for models: in YAML."""
    m = collect_model_meta_keys_by_name_from_project(
        tmp_path,
        {"m": "analyses", "n": "macros"},
    )
    assert m == {}


def test_collect_with_target_model_names_filters_result(tmp_path: Path) -> None:
    models = tmp_path / "models"
    models.mkdir()
    (models / "schema.yml").write_text(
        """version: 2
models:
  - name: a
    meta:
      ka: 1
  - name: b
    config:
      meta:
        kb: 1
""",
        encoding="utf-8",
    )
    m = collect_model_meta_keys_by_name_from_project(
        tmp_path,
        {"models": "models"},
        target_model_names={"b"},
    )
    assert m == {"b": frozenset({"kb"})}


def test_resolver_returns_only_targeted_model_keys(tmp_path: Path) -> None:
    models = tmp_path / "models"
    models.mkdir()
    (models / "schema.yml").write_text(
        """version: 2
models:
  - name: target
    meta:
      k: 1
  - name: other
    meta:
      x: 1
""",
        encoding="utf-8",
    )
    resolver = SchemaYmlModelMetaResolver(
        tmp_path,
        {"models": "models"},
        target_model_names={"target"},
    )
    assert resolver.get_keys("target") == frozenset({"k"})
    assert resolver.get_keys("other") == frozenset()
