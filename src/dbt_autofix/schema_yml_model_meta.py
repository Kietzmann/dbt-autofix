"""Collect schema.yml model meta keys used for get -> meta_get gating."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping

from yaml import safe_load


def _string_keys_in_mapping(m: Any) -> set[str]:
    if not isinstance(m, dict):
        return set()
    return {k for k in m if isinstance(k, str)}


def _meta_string_keys_from_model_entry(entry: dict) -> set[str]:
    """Keys in config.meta and top-level meta for a dbt schema model entry."""
    out: set[str] = set()
    cfg = entry.get("config")
    if isinstance(cfg, dict) and isinstance(cfg.get("meta"), dict):
        out |= _string_keys_in_mapping(cfg["meta"])
    if isinstance(entry.get("meta"), dict):
        out |= _string_keys_in_mapping(entry["meta"])
    return out


def _merge_model_meta_from_doc(
    out: MutableMapping[str, set[str]], data: Any, target_model_names: set[str] | None = None
) -> None:
    if not isinstance(data, dict) or "models" not in data:
        return
    models = data["models"]
    if not isinstance(models, list):
        return
    for item in models:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            continue
        if target_model_names is not None and name not in target_model_names:
            continue
        keys = _meta_string_keys_from_model_entry(item)
        if not keys:
            continue
        if name not in out:
            out[name] = set()
        out[name] |= keys


def collect_model_meta_keys_by_name_from_project(
    project_root: Path,
    dbt_paths_to_node_type: Mapping[str, str],
    target_model_names: set[str] | None = None,
) -> dict[str, frozenset[str]]:
    """For each dbt *models* path under ``project_root``, read ``**/*.{yml,yaml}`` and merge.

    ``models:`` entries' ``config.meta`` / ``meta`` string keys into
    ``model_name -> set of keys``.

    Model *name* is the ``name:`` field in the YAML. Lookup by SQL/Python **file stem**
    matches dbt's default when the model name equals the file name.
    """

    if target_model_names is not None and not target_model_names:
        return {}

    out: dict[str, set[str]] = {}
    name_regex = _target_name_line_regex(target_model_names) if target_model_names else None

    for rel, node_type in dbt_paths_to_node_type.items():
        if node_type != "models":
            continue
        base = (project_root / rel).resolve()
        if not base.is_dir():
            continue
        for yml in sorted(base.glob("**/*.yml")):
            _load_and_merge_file(out, yml, target_model_names=target_model_names, name_regex=name_regex)
        for yml in sorted(base.glob("**/*.yaml")):
            _load_and_merge_file(out, yml, target_model_names=target_model_names, name_regex=name_regex)

    return {k: frozenset(v) for k, v in out.items()}


def _target_name_line_regex(target_model_names: set[str]) -> re.Pattern[str]:
    escaped = "|".join(sorted(re.escape(name) for name in target_model_names))
    return re.compile(rf"(?m)^\s*-\s*name:\s*['\"]?(?:{escaped})['\"]?\s*$")


def _maybe_contains_relevant_model_meta(
    text: str,
    target_model_names: set[str] | None,
    name_regex: re.Pattern[str] | None,
) -> bool:
    # Cheap fast-paths before YAML parsing.
    if "models:" not in text or "meta:" not in text:
        return False
    if target_model_names is None:
        return True
    if name_regex is None:
        return False
    return bool(name_regex.search(text))


def _load_and_merge_file(
    out: dict[str, set[str]],
    yml: Path,
    *,
    target_model_names: set[str] | None,
    name_regex: re.Pattern[str] | None,
) -> None:
    try:
        raw = yml.read_text(encoding="utf-8")
    except OSError:
        return
    if not _maybe_contains_relevant_model_meta(raw, target_model_names, name_regex):
        return

    try:
        data = safe_load(raw)
    except Exception:
        return
    if data is None:
        return
    _merge_model_meta_from_doc(out, data, target_model_names=target_model_names)


class SchemaYmlModelMetaResolver:
    """Lazy loader for schema.yml model meta keys."""

    def __init__(
        self,
        project_root: Path,
        dbt_paths_to_node_type: Mapping[str, str],
        target_model_names: Iterable[str] | None = None,
    ) -> None:
        self._project_root = project_root
        self._dbt_paths_to_node_type = dict(dbt_paths_to_node_type)
        self._target_model_names = set(target_model_names) if target_model_names is not None else None
        self._cache: dict[str, frozenset[str]] | None = None

    def get_keys(self, model_name: str) -> frozenset[str]:
        if self._target_model_names is not None and model_name not in self._target_model_names:
            return frozenset()
        if self._cache is None:
            self._cache = collect_model_meta_keys_by_name_from_project(
                self._project_root,
                self._dbt_paths_to_node_type,
                target_model_names=self._target_model_names,
            )
        return self._cache.get(model_name, frozenset())
