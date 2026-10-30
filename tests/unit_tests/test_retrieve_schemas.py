import os
import time

import httpx
import pytest

from dbt_autofix.retrieve_schemas import (
    LATEST_VERSION_CACHE_TTL_SECONDS,
    SchemaSpecs,
    get_cache_dir,
    get_fusion_dbt_project_schema,
    get_fusion_latest_version,
    get_fusion_yml_schema,
)


def test_dict_config_analysis_ignores_boolean_schema_references(monkeypatch):
    schema = {
        "definitions": {
            "ProjectModelConfig": {
                "properties": {
                    "+lifetime": {"anyOf": [{"$ref": "#/definitions/AnyValue"}, {"type": "null"}]},
                    "+persist_docs": {"anyOf": [{"$ref": "#/definitions/PersistDocs"}, {"type": "null"}]},
                }
            },
            "AnyValue": True,
            "PersistDocs": {
                "type": "object",
                "properties": {"columns": {"type": "boolean"}, "relation": {"type": "boolean"}},
            },
        }
    }
    schema_specs = object.__new__(SchemaSpecs)
    schema_specs._dict_config_cache = None
    schema_specs._schema_version = "test"
    schema_specs.client = object()
    monkeypatch.setattr(
        "dbt_autofix.retrieve_schemas.get_fusion_dbt_project_schema",
        lambda client, version: schema,
    )

    analysis = schema_specs.get_dict_config_analysis()

    assert analysis == {
        "specific_properties": {"persist_docs": {"columns", "relation"}},
        "open_ended": set(),
    }


class TestSchemaCache:
    VERSIONS_JSON = '{"latest": {"tag": "v2.0.9"}}'
    YML_SCHEMA = '{"old": true}\n----------------------------------------------\n{"properties": {}}'
    PROJECT_SCHEMA = '{"properties": {}}'

    @pytest.fixture
    def cache_dir(self, tmp_path, monkeypatch):
        cache_dir = tmp_path / "cache"
        monkeypatch.setenv("DBT_AUTOFIX_CACHE_DIR", str(cache_dir))
        return cache_dir

    def _client(self, requests, fail=False):
        def handler(request):
            requests.append(request.url.path.rsplit("/", 1)[-1])
            if fail:
                raise httpx.ConnectError("offline", request=request)
            name = request.url.path.rsplit("/", 1)[-1]
            body = {"versions.json": self.VERSIONS_JSON}.get(name) or (
                self.YML_SCHEMA if "dbt-yaml-files" in name else self.PROJECT_SCHEMA
            )
            return httpx.Response(200, text=body)

        return httpx.Client(transport=httpx.MockTransport(handler))

    def test_versioned_schema_is_downloaded_once(self, cache_dir):
        requests = []
        client = self._client(requests)
        assert get_fusion_yml_schema(client, "v2.0.9") == {"properties": {}}
        assert get_fusion_yml_schema(client, "v2.0.9") == {"properties": {}}
        assert requests == ["fs-schema-dbt-yaml-files-v2.0.9.json"]
        assert (cache_dir / "fs-schema-dbt-yaml-files-v2.0.9.json").read_text() == self.YML_SCHEMA

    def test_versioned_schema_cache_never_expires(self, cache_dir):
        get_fusion_dbt_project_schema(self._client([]), "v2.0.9")
        cache_file = cache_dir / "fs-schema-dbt-project-v2.0.9.json"
        os.utime(cache_file, (0, 0))
        requests = []
        get_fusion_dbt_project_schema(self._client(requests), "v2.0.9")
        assert requests == []

    def test_latest_version_is_refreshed_after_ttl(self, cache_dir):
        requests = []
        client = self._client(requests)
        assert get_fusion_latest_version(client) == "v2.0.9"
        assert get_fusion_latest_version(client) == "v2.0.9"
        assert requests == ["versions.json"]

        expired = time.time() - LATEST_VERSION_CACHE_TTL_SECONDS - 1
        os.utime(cache_dir / "versions.json", (expired, expired))
        assert get_fusion_latest_version(client) == "v2.0.9"
        assert requests == ["versions.json", "versions.json"]

    def test_stale_cache_is_used_when_offline(self, cache_dir):
        get_fusion_latest_version(self._client([]))
        os.utime(cache_dir / "versions.json", (0, 0))
        requests = []
        assert get_fusion_latest_version(self._client(requests, fail=True)) == "v2.0.9"
        assert requests == ["versions.json"]

    def test_offline_without_cache_raises(self, cache_dir):
        with pytest.raises(httpx.ConnectError):
            get_fusion_latest_version(self._client([], fail=True))

    def test_corrupt_cache_is_downloaded_again(self, cache_dir):
        cache_dir.mkdir()
        (cache_dir / "fs-schema-dbt-project-v2.0.9.json").write_text("{not json")
        requests = []
        assert get_fusion_dbt_project_schema(self._client(requests), "v2.0.9") == {"properties": {}}
        assert requests == ["fs-schema-dbt-project-v2.0.9.json"]

    def test_empty_cache_dir_disables_caching(self, monkeypatch):
        monkeypatch.setenv("DBT_AUTOFIX_CACHE_DIR", "")
        assert get_cache_dir() is None
        requests = []
        client = self._client(requests)
        get_fusion_latest_version(client)
        get_fusion_latest_version(client)
        assert requests == ["versions.json", "versions.json"]

    def test_default_cache_dir_follows_xdg(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DBT_AUTOFIX_CACHE_DIR", raising=False)
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        assert get_cache_dir() == tmp_path / "dbt-autofix"

    def test_unwritable_cache_dir_still_returns_schema(self, tmp_path, monkeypatch):
        not_a_dir = tmp_path / "file"
        not_a_dir.write_text("")
        monkeypatch.setenv("DBT_AUTOFIX_CACHE_DIR", str(not_a_dir))
        assert get_fusion_latest_version(self._client([])) == "v2.0.9"
