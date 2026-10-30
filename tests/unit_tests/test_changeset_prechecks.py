"""Tests that YAML changesets skip parsing files their rule cannot change."""

from unittest.mock import MagicMock, patch

import pytest
from ruamel.yaml.comments import CommentedMap

from dbt_autofix.refactors import static_analysis
from dbt_autofix.refactors.changesets import dbt_schema_yml
from dbt_autofix.refactors.changesets.dbt_schema_yml import (
    changeset_owner_properties_yml_str,
    changeset_remove_duplicate_models,
    changeset_remove_extra_tabs,
)
from dbt_autofix.refactors.results import YMLContent, YMLRefactorConfig
from dbt_autofix.refactors.static_analysis import changeset_normalize_static_analysis_yml

SOURCES_YML = "sources:\n  - name: raw\n    tables:\n      - name: orders\n"


def _content(yml_str: str) -> YMLContent:
    return YMLContent(original_str=yml_str, original_parsed=CommentedMap(), current_str=yml_str)


def _config() -> YMLRefactorConfig:
    schema_specs = MagicMock()
    schema_specs.nodes_with_owner = ["groups", "exposures"]
    schema_specs.owner_properties = ["name", "email"]
    return YMLRefactorConfig(schema_specs=schema_specs)


@pytest.mark.parametrize(
    "changeset, yml_str",
    [
        (changeset_owner_properties_yml_str, SOURCES_YML),
        # owner under a model's meta is not a groups/exposures owner block
        (changeset_owner_properties_yml_str, "models:\n  - name: m\n    meta:\n      owner: me\n"),
        (changeset_remove_duplicate_models, SOURCES_YML),
        (changeset_normalize_static_analysis_yml, SOURCES_YML),
    ],
)
def test_rule_skips_parse_when_trigger_text_is_absent(changeset, yml_str):
    with (
        patch.object(dbt_schema_yml, "load_yaml") as schema_load_yaml,
        patch.object(static_analysis, "load_yaml") as static_load_yaml,
    ):
        result = changeset(_content(yml_str), _config())
    schema_load_yaml.assert_not_called()
    static_load_yaml.assert_not_called()
    assert not result.refactored
    assert result.refactored_yaml == yml_str


def test_remove_extra_tabs_skips_lint_without_tab_characters():
    with patch.object(dbt_schema_yml.yamllint.linter, "run") as lint:
        result = changeset_remove_extra_tabs(_content(SOURCES_YML), _config())
    lint.assert_not_called()
    assert not result.refactored


@pytest.mark.parametrize(
    "changeset, yml_str",
    [
        (
            changeset_owner_properties_yml_str,
            "groups:\n  - name: finance\n    owner:\n      name: me\n      slack: '#finance'\n",
        ),
        (changeset_remove_duplicate_models, "models:\n  - name: m\n  - name: m\n"),
        (changeset_normalize_static_analysis_yml, "models:\n  - name: m\n    config:\n      static_analysis: false\n"),
        (changeset_remove_extra_tabs, "models:\n  - name: m\n\t  description: tabbed\n"),
    ],
)
def test_rule_still_applies_when_trigger_text_is_present(changeset, yml_str):
    assert changeset(_content(yml_str), _config()).refactored
