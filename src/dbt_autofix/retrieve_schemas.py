import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import httpx


@dataclass
class YAMLSpecs:
    allowed_config_fields: set[str]
    allowed_properties: set[str]

    def __post_init__(self):
        self.allowed_config_fields_without_meta = self.allowed_config_fields - {"meta"}


@dataclass
class DbtProjectSpecs:
    allowed_config_fields_dbt_project_with_plus: set[str]

    def __post_init__(self):
        self.allowed_config_fields_dbt_project = set(
            [conf[1:] for conf in self.allowed_config_fields_dbt_project_with_plus]
        )


class SchemaSpecs:
    def __init__(self, version: Optional[str] = None, disable_ssl_verification: bool = False):
        # Create single client for getting schemas from CDN
        # add retries for transient connection issues
        self.disable_ssl_verification = disable_ssl_verification
        self.transport = httpx.HTTPTransport(retries=3)
        self.client = httpx.Client(transport=self.transport, verify=not disable_ssl_verification)

        # Resolve "latest" once, so every schema used in this run comes from the same version
        self._schema_version = version or get_fusion_latest_version(self.client)
        self.yaml_specs_per_node_type, self.dbtproject_specs_per_node_type, self.valid_top_level_yaml_fields = (
            self._get_specs(self._schema_version)
        )
        self.owner_properties = ["name", "email"]
        self.nodes_with_owner = ["groups", "exposures"]
        # Cache dict config analysis
        self._dict_config_cache = None

    def _get_specs(
        self, version: Optional[str] = None
    ) -> tuple[dict[str, YAMLSpecs], dict[str, DbtProjectSpecs], list[str]]:
        if os.getenv("DEBUG"):
            logging.basicConfig(level=logging.INFO)

        if version is None:
            version = get_fusion_latest_version(self.client)
        yml_schema = get_fusion_yml_schema(self.client, version)
        dbt_project_schema = get_fusion_dbt_project_schema(self.client, version)

        valid_top_level_yaml_fields = list(yml_schema["properties"].keys())

        node_type_to_config_key_aliases = {
            "models": ["dataset", "project", "data_space"],
            "seeds": ["dataset", "project", "data_space"],
            "snapshots": ["dataset", "project", "data_space"],
            "tests": ["dataset", "project", "data_space"],
            "sources": ["dataset", "project", "data_space"],
        }

        node_type_to_config_key_aliases_with_plus = {
            node_type: [f"+{alias}" for alias in aliases]
            for node_type, aliases in node_type_to_config_key_aliases.items()
        }

        # "models"
        model_property_field_name, model_config_field_name = self._get_yml_schema_fields(yml_schema, "models")
        yaml_specs_models = YAMLSpecs(
            allowed_config_fields=set(yml_schema["definitions"][model_config_field_name]["properties"]).union(
                node_type_to_config_key_aliases["models"]
            ),
            allowed_properties=set(yml_schema["definitions"][model_property_field_name]["properties"]),
        )
        model_property_field_name_dbt_project = self._get_dbt_project_schema_fields(dbt_project_schema, "models")
        dbtproject_specs_models = DbtProjectSpecs(
            allowed_config_fields_dbt_project_with_plus=set(
                dbt_project_schema["definitions"][model_property_field_name_dbt_project]["properties"],
            ).union(node_type_to_config_key_aliases_with_plus["models"]),
        )

        # "sources"
        source_property_field_name, source_config_field_name = self._get_yml_schema_fields(yml_schema, "sources")
        yaml_specs_sources = YAMLSpecs(
            allowed_config_fields=set(yml_schema["definitions"][source_config_field_name]["properties"]),
            allowed_properties=set(yml_schema["definitions"][source_property_field_name]["properties"]).union(
                node_type_to_config_key_aliases["sources"]
            ),
        )
        source_property_field_name_dbt_project = self._get_dbt_project_schema_fields(dbt_project_schema, "sources")
        dbtproject_specs_sources = DbtProjectSpecs(
            allowed_config_fields_dbt_project_with_plus=set(
                dbt_project_schema["definitions"][source_property_field_name_dbt_project]["properties"]
            ).union(node_type_to_config_key_aliases_with_plus["sources"]),
        )

        # "snapshots"
        snapshot_property_field_name, snapshot_config_field_name = self._get_yml_schema_fields(yml_schema, "snapshots")
        yaml_specs_snapshots = YAMLSpecs(
            allowed_config_fields=set(yml_schema["definitions"][snapshot_config_field_name]["properties"]).union(
                node_type_to_config_key_aliases["snapshots"]
            ),
            allowed_properties=set(yml_schema["definitions"][snapshot_property_field_name]["properties"]),
        )
        snapshot_property_field_name_dbt_project = self._get_dbt_project_schema_fields(dbt_project_schema, "snapshots")
        dbtproject_specs_snapshots = DbtProjectSpecs(
            allowed_config_fields_dbt_project_with_plus=set(
                dbt_project_schema["definitions"][snapshot_property_field_name_dbt_project]["properties"]
            ).union(node_type_to_config_key_aliases_with_plus["snapshots"]),
        )

        # "seeds"
        seed_property_field_name, seed_config_field_name = self._get_yml_schema_fields(yml_schema, "seeds")
        yaml_specs_seeds = YAMLSpecs(
            allowed_config_fields=set(yml_schema["definitions"][seed_config_field_name]["properties"]).union(
                node_type_to_config_key_aliases["seeds"]
            ),
            allowed_properties=set(yml_schema["definitions"][seed_property_field_name]["properties"]),
        )
        seed_property_field_name_dbt_project = self._get_dbt_project_schema_fields(dbt_project_schema, "seeds")
        dbtproject_specs_seeds = DbtProjectSpecs(
            allowed_config_fields_dbt_project_with_plus=set(
                dbt_project_schema["definitions"][seed_property_field_name_dbt_project]["properties"]
            ).union(node_type_to_config_key_aliases_with_plus["seeds"]),
        )

        # "exposures"
        exposure_property_field_name, exposure_config_field_name = self._get_yml_schema_fields(yml_schema, "exposures")
        yaml_specs_exposures = YAMLSpecs(
            allowed_config_fields=set(yml_schema["definitions"][exposure_config_field_name]["properties"]),
            allowed_properties=set(yml_schema["definitions"][exposure_property_field_name]["properties"]),
        )
        exposure_property_field_name_dbt_project = self._get_dbt_project_schema_fields(dbt_project_schema, "exposures")
        dbtproject_specs_exposures = DbtProjectSpecs(
            allowed_config_fields_dbt_project_with_plus=set(
                dbt_project_schema["definitions"][exposure_property_field_name_dbt_project]["properties"]
            ),
        )

        # "tables"
        table_property_field_name, table_config_field_name = self._get_yml_schema_subfields(
            yml_schema, source_property_field_name, "tables"
        )
        yaml_specs_tables = YAMLSpecs(
            allowed_config_fields=set(yml_schema["definitions"][table_config_field_name]["properties"]),
            allowed_properties=set(yml_schema["definitions"][table_property_field_name]["properties"]),
        )

        # "columns"
        column_property_field_name, column_config_field_name = self._get_yml_schema_subfields(
            yml_schema, model_property_field_name, "columns"
        )
        columns = YAMLSpecs(
            allowed_config_fields=set(yml_schema["definitions"][column_config_field_name]["properties"]),
            allowed_properties=set(yml_schema["definitions"][column_property_field_name]["properties"]),
        )

        # "tests" or "data_tests"
        test_property_field_name, test_config_field_name = self._get_yml_schema_fields(yml_schema, "tests")
        yaml_specs_tests = YAMLSpecs(
            allowed_config_fields=set(yml_schema["definitions"][test_config_field_name]["properties"]).union(
                node_type_to_config_key_aliases["tests"]
            ),
            allowed_properties=set(yml_schema["definitions"][test_property_field_name]["properties"]),
        )
        test_property_field_name_dbt_project = self._get_dbt_project_schema_fields(dbt_project_schema, "tests")
        dbtproject_specs_tests = DbtProjectSpecs(
            allowed_config_fields_dbt_project_with_plus=set(
                dbt_project_schema["definitions"][test_property_field_name_dbt_project]["properties"]
            ).union(node_type_to_config_key_aliases_with_plus["tests"]),
        )

        # "groups"
        group_property_field_name, group_config_field_name = self._get_yml_schema_fields(yml_schema, "groups")
        yaml_specs_groups = YAMLSpecs(
            allowed_config_fields=set(yml_schema["definitions"][group_config_field_name]["properties"]),
            allowed_properties=set(yml_schema["definitions"][group_property_field_name]["properties"]),
        )

        # "analyses"
        analysis_property_field_name, analysis_config_field_name = self._get_yml_schema_fields(yml_schema, "analyses")
        yaml_specs_analyses = YAMLSpecs(
            allowed_config_fields=set(yml_schema["definitions"][analysis_config_field_name]["properties"]),
            allowed_properties=set(yml_schema["definitions"][analysis_property_field_name]["properties"]),
        )

        # "unit_tests"
        unit_tests_property_field_name, unit_tests_config_field_name = self._get_yml_schema_fields(
            yml_schema, "unit_tests"
        )
        yaml_specs_unit_tests = YAMLSpecs(
            allowed_config_fields=set(yml_schema["definitions"][unit_tests_config_field_name]["properties"]),
            allowed_properties=set(yml_schema["definitions"][unit_tests_property_field_name]["properties"]),
        )
        unit_tests_property_field_name_dbt_project = self._get_dbt_project_schema_fields(
            dbt_project_schema, "unit_tests"
        )
        dbtproject_specs_unit_tests = DbtProjectSpecs(
            allowed_config_fields_dbt_project_with_plus=set(
                dbt_project_schema["definitions"][unit_tests_property_field_name_dbt_project]["properties"]
            ),
        )

        return (
            {
                "models": yaml_specs_models,
                "seeds": yaml_specs_seeds,
                "sources": yaml_specs_sources,
                "snapshots": yaml_specs_snapshots,
                "tests": yaml_specs_tests,  # tests can be at the top level, or nested under nodes or nested under nodes columns
                "data_tests": yaml_specs_tests,  # data_tests can be at the top level, or nested under nodes or nested under nodes columns
                "exposures": yaml_specs_exposures,
                "tables": yaml_specs_tables,  # tables is nested under sources
                "columns": columns,  # columns is nested under models
                "groups": yaml_specs_groups,
                "analyses": yaml_specs_analyses,
                "unit_tests": yaml_specs_unit_tests,
            },
            {
                "models": dbtproject_specs_models,
                "seeds": dbtproject_specs_seeds,
                # "semantic-models": dbtproject_specs_saved_queries, -- there is an issue with those specs in 165
                "snapshots": dbtproject_specs_snapshots,
                "sources": dbtproject_specs_sources,
                "tests": dbtproject_specs_tests,
                "data_tests": dbtproject_specs_tests,
                "exposures": dbtproject_specs_exposures,
                "unit-tests": dbtproject_specs_unit_tests,
            },
            valid_top_level_yaml_fields,
        )

    def _get_yml_schema_fields(self, yml_schema: dict, node_type: str) -> tuple[str, str]:
        property_field_name = yml_schema["properties"][node_type]["items"]["$ref"].split("/")[-1]
        config_field_name = yml_schema["definitions"][property_field_name]["properties"]["config"]["anyOf"][0][
            "$ref"
        ].split("/")[-1]
        return property_field_name, config_field_name

    def _get_yml_schema_subfields(self, yml_schema: dict, definition: str, node_type: str) -> tuple[str, str]:
        property_field_name = yml_schema["definitions"][definition]["properties"][node_type]["items"]["$ref"].split(
            "/"
        )[-1]
        config_field_name = yml_schema["definitions"][property_field_name]["properties"]["config"]["anyOf"][0][
            "$ref"
        ].split("/")[-1]
        return property_field_name, config_field_name

    def _get_dbt_project_schema_fields(self, yml_schema: dict, node_type: str) -> str:
        property_field_name = yml_schema["properties"][node_type]["anyOf"][0]["$ref"].split("/")[-1]
        return property_field_name

    def get_dict_config_analysis(self) -> dict[str, Any]:
        """Get analysis of which dict configs have specific properties vs accept any key-value pairs.

        Returns:
            dict with two keys:
                - 'specific_properties': dict mapping config name to its allowed properties
                - 'open_ended': set of config names that accept any key-value pairs
        """
        if self._dict_config_cache is None:
            # Get the schema
            version = self._schema_version or get_fusion_latest_version(self.client)
            schema = get_fusion_dbt_project_schema(self.client, version)

            specific_properties = {}
            open_ended = set()

            # Check all node type definitions
            for def_name, definition in schema.get("definitions", {}).items():
                # Skip if definition is not a dict (some are boolean values)
                if not isinstance(definition, dict):
                    continue
                if "properties" in definition:
                    for prop_name, prop_spec in definition["properties"].items():
                        # Skip if it doesn't start with +
                        if not prop_name.startswith("+"):
                            continue

                        config_name = prop_name[1:]  # Remove + prefix

                        # Check if this is an object/dict type
                        if isinstance(prop_spec, dict):
                            # Check for type: ["object", "null"] pattern
                            types = prop_spec.get("type", [])
                            if isinstance(types, list) and "object" in types:
                                if "properties" in prop_spec:
                                    # Has specific properties defined
                                    specific_properties[config_name] = set(prop_spec["properties"].keys())
                                elif prop_spec.get("additionalProperties") is not False:
                                    # If additionalProperties is not explicitly False, it accepts any key-value
                                    open_ended.add(config_name)

                            # Check anyOf patterns
                            elif "anyOf" in prop_spec:
                                for option in prop_spec["anyOf"]:
                                    # Check for $ref to another definition
                                    if "$ref" in option:
                                        ref_name = option["$ref"].split("/")[-1]
                                        if ref_name in schema.get("definitions", {}):
                                            ref_def = schema["definitions"][ref_name]
                                            if isinstance(ref_def, dict) and ref_def.get("type") == "object":
                                                if "properties" in ref_def:
                                                    # Has specific properties defined
                                                    specific_properties[config_name] = set(ref_def["properties"].keys())
                                                elif ref_def.get("additionalProperties") is not False:
                                                    # If additionalProperties is not explicitly False, it accepts any key-value
                                                    open_ended.add(config_name)
                                        break

            self._dict_config_cache = {"specific_properties": specific_properties, "open_ended": open_ended}

        return self._dict_config_cache


FUSION_CDN_URL = "https://public.cdn.getdbt.com/fs"
CACHE_DIR_ENV_VAR = "DBT_AUTOFIX_CACHE_DIR"
# versions.json moves when a new Fusion version is released; versioned schema files never change
LATEST_VERSION_CACHE_TTL_SECONDS = 24 * 60 * 60


def get_cache_dir() -> Optional[Path]:
    """Directory where downloaded schemas are cached, or None when caching is disabled.

    DBT_AUTOFIX_CACHE_DIR overrides the location and disables caching when set to an empty string.
    Otherwise $XDG_CACHE_HOME/dbt-autofix, falling back to ~/.cache/dbt-autofix.
    """
    cache_dir = os.environ.get(CACHE_DIR_ENV_VAR)
    if cache_dir is not None:
        return Path(cache_dir) if cache_dir else None
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "dbt-autofix"


def _write_cache_file(cache_file: Path, text: str) -> None:
    """Write a cache file atomically; failing to write it is not an error.

    Writing a temporary file and renaming it means concurrent runs (parallel pre-commit batches)
    never read a partially written file.
    """
    tmp_name = None
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=cache_file.parent, prefix=f".{cache_file.name}.")
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(text)
        os.replace(tmp_name, cache_file)
    except OSError as e:
        logging.info(f"Could not write schema cache {cache_file}: {e}")
        if tmp_name is not None and os.path.exists(tmp_name):
            os.remove(tmp_name)


def _fetch_cached(
    client: httpx.Client, url: str, parse: Callable[[str], Any], max_age_seconds: Optional[float] = None
) -> Any:
    """GET url and parse the body, reusing a cached copy that is fresh and parses.

    When the download fails, a stale cached copy is used instead, so runs keep working offline.
    """
    cache_dir = get_cache_dir()
    cache_file = cache_dir / url.rsplit("/", 1)[-1] if cache_dir else None

    stale_text: Optional[str] = None
    if cache_file is not None and cache_file.is_file():
        try:
            cached_text = cache_file.read_text(encoding="utf-8")
            cached = parse(cached_text)
        except (OSError, ValueError, KeyError):
            pass  # unreadable or corrupt cache: download again
        else:
            age_seconds = time.time() - cache_file.stat().st_mtime
            if max_age_seconds is None or age_seconds < max_age_seconds:
                return cached
            stale_text = cached_text

    try:
        logging.info(f"Downloading {url}")
        response = client.get(url)
        response.raise_for_status()
    except httpx.HTTPError as e:
        if stale_text is None:
            raise
        logging.warning(f"Could not download {url} ({e}); using the cached copy from {cache_file}")
        return parse(stale_text)

    result = parse(response.text)
    if cache_file is not None:
        _write_cache_file(cache_file, response.text)
    return result


def get_fusion_latest_version(client: httpx.Client) -> str:
    return _fetch_cached(
        client,
        f"{FUSION_CDN_URL}/versions.json",
        lambda text: json.loads(text)["latest"]["tag"],
        max_age_seconds=LATEST_VERSION_CACHE_TTL_SECONDS,
    )


def _parse_fusion_yml_schema(text: str) -> dict:
    # for some reason we have 2 different schemas now in the response
    return json.loads(text.split("----------------------------------------------")[-1])


def get_fusion_yml_schema(client: httpx.Client, version: str) -> dict:
    logging.info(f"Getting fusion yml schema for version {version}")
    return _fetch_cached(
        client, f"{FUSION_CDN_URL}/schemas/fs-schema-dbt-yaml-files-{version}.json", _parse_fusion_yml_schema
    )


def get_fusion_dbt_project_schema(client: httpx.Client, version: str) -> dict:
    logging.info(f"Getting fusion dbt project schema for version {version}")
    return _fetch_cached(client, f"{FUSION_CDN_URL}/schemas/fs-schema-dbt-project-{version}.json", json.loads)
