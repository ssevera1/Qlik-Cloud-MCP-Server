"""Contract tests for the REST-backed tools: every tool builds a valid request and a clean payload."""

import json
from typing import Any

import pytest

from qlik_mcp_server.config import Config
from qlik_mcp_server.server import create_server
from qlik_mcp_server.tools.rest_catalog import REST_TOOLS
from qlik_mcp_server.tools.rest_tools import P

from .test_tool_contracts import FakeEngineClient

DATASET = {
    "id": "ds-1", "name": "Sales", "technicalName": "sales.csv", "qri": "qri:qdf:space://sp1#ds-1", "spaceId": "sp1",
    "operational": {"rowCount": 40, "lastLoadTime": "2026-08-30T10:00:00Z", "lastUpdateTime": "2026-08-31T10:00:00Z", "status": "ready"},
    "schema": {"dataFields": [{"name": "Region", "dataType": {"type": "STRING"}, "nullable": True},
                              {"name": "Sales", "dataType": {"type": "DOUBLE"}, "nullable": False}]},
}
PROFILE = {"data": [{
    "meta": {"status": "FINISHED", "computationEndTime": "2026-08-31T10:00:00Z"},
    "profiles": [{"name": "sales", "numberOfRows": 40, "fieldProfiles": [
        {"name": "Region", "dataType": "STRING", "distinctValueCount": 3, "nullValueCount": 0,
         "mostFrequentValues": [{"value": "East", "frequency": 20}], "sampleValues": ["East", "West"]}]}],
    "samples": [{"name": "sales", "fieldNames": ["Region", "Sales"], "records": [{"values": ["East", "100"]}, {"values": ["West", "50"]}]}],
}]}


class RecordingRestClient:
    """Records every call and answers with canned, path-aware JSON."""

    def __init__(self):
        self.calls: list[dict] = []
        self.run_polls = 0

    async def call(self, method, path, params=None, json=None, text=False, cache=False):
        self.calls.append({"method": method, "path": path, "params": params, "json": json, "text": text})
        answer = self._answer(method, path, params or {}, json)
        if text and isinstance(answer, dict) and set(answer) == {"raw_content"}:
            return answer["raw_content"]  # the real client returns text bodies as str
        return answer

    async def fetch_text_url(self, url, max_chars=20000):
        self.calls.append({"method": "GET-URL", "path": url})
        return "block 1 ok\nblock 2 failed"

    async def search_items(self, query, resource_type=None, space_id=None, limit=20):
        return []

    async def get_app(self, app_id):
        return {"id": app_id, "name": "Sales"}

    async def get_app_data_metadata(self, app_id):
        return {}

    def _answer(self, method, path, params, body) -> Any:
        if text_like(path):
            return {"raw_content": "# Product docs\n\nMarkdown body"}
        if path == "/api/v1/automations" and method == "GET":
            return {"data": [{"id": "auto-1", "name": "Daily sync", "runMode": "scheduled", "lastRunStatus": "finished",
                              "schedules": [{"interval": 86400}], "workspace": {"blocks": [{"type": "StartBlock", "inputs": [
                                  {"name": "region", "label": "Region", "type": "string", "required": True}]}]}}]}
        if path.startswith("/api/v1/automations/auto-1/runs/run-1/actions/export"):
            return {"url": "https://tenant.us.qlikcloud.com/logs/run-1"}
        if path.startswith("/api/v1/automations/auto-1/runs/run-1"):
            self.run_polls += 1
            return {"id": "run-1", "status": "running" if self.run_polls < 2 else "finished", "startTime": "2026-09-01T00:00:00Z"}
        if path.startswith("/api/v1/automations/auto-1/runs"):
            return {"data": [{"id": "run-1", "status": "finished", "startTime": "2026-09-01T00:00:00Z"},
                             {"id": "run-0", "status": "failed", "startTime": "2026-08-31T00:00:00Z"}]}
        if path.startswith("/api/v1/automations/auto-1"):
            return {"id": "auto-1", "name": "Daily sync", "description": "d", "runMode": "scheduled", "maxConcurrentRuns": 1,
                    "schedules": [{"interval": 86400, "timezone": "UTC", "startAt": "2026-01-01T00:00:00Z"}],
                    "workspace": {"blocks": [{"type": "StartBlock", "inputs": [{"name": "region", "label": "Region", "type": "string"}]}]}}
        if path.startswith("/api/v1/automation-connectors"):
            return {"data": [{"id": "conn-slack", "name": "Slack", "hasWebhooks": True}]}
        if path.startswith("/api/v1/automation-connections"):
            return {"data": [{"id": "c1", "name": "My Slack", "connectorId": "conn-slack", "isConnected": True}]}
        if path.startswith("/api/v1/glossaries") and path.endswith("/terms") and method == "GET":
            return {"data": [{"id": "t1", "name": "Revenue", "status": {"type": "verified"}, "glossaryId": "g1"}]}
        if path.startswith("/api/v1/glossaries/g1/terms/t1/links"):
            return {"data": [{"id": "l1", "type": "definition", "resourceType": "app", "resourceId": "app-1"}]} if method == "GET" else {"id": "l2", "type": "definition"}
        if path.startswith("/api/v1/glossaries/g1/terms/t1"):
            return {"id": "t1", "name": "Revenue", "status": {"type": "verified"}, "linksTo": [], "relatesTo": []}
        if path.startswith("/api/v1/glossaries/g1/categories"):
            return {"data": [{"id": "cat1", "name": "Finance"}]} if method == "GET" else {"id": "cat2", "name": "New"}
        if path.startswith("/api/v1/glossaries/g1/actions/export"):
            return {"id": "g1", "name": "Business", "terms": [], "categories": []}
        if path.startswith("/api/v1/glossaries"):
            return {"data": [{"id": "g1", "name": "Business"}]} if method == "GET" and path == "/api/v1/glossaries" else {"id": "g1", "name": "Business"}
        if path.endswith("/profiles"):
            return PROFILE
        if path.startswith("/api/v1/data-sets/"):
            return DATASET
        if path.startswith("/api/data-governance/trust-scores"):
            return {"data": [{"datasetId": "ds-1", "score": 4.2, "axes": [{"id": "validity", "score": 5}]}]}
        if path == "/api/v1/items":
            return {"data": [{"id": "item-dp", "resourceId": "dp-1", "name": "Sales product", "resourceType": "dataproduct"}]}
        if path.startswith("/api/data-governance/data-products/dp-1"):
            return {"id": "dp-1", "name": "Sales product", "datasetIds": ["ds-1"], "activated": True, "readMe": "# hi"}
        if path.startswith("/api/data-governance/data-products"):
            return {"id": "dp-2", "name": "New product", "datasetIds": []}
        if path.startswith("/api/v1/data-qualities/computations/"):
            return {"status": "RUNNING"}
        if path == "/api/v1/data-qualities/computations":
            return {"computationId": "comp-1"}
        if path.startswith("/api/v1/lineage-graphs/impact/"):
            return {"graph": {"nodes": {"qri:app:sense://a": {"label": "App", "metadata": {"type": "APP", "id": "qri:app:sense://a"}}}, "edges": []}}
        if path.startswith("/api/v1/lineage-graphs/nodes/"):
            return {"graph": {"nodes": {"qri:db:x": {"label": "DB", "metadata": {"type": "DATABASE", "id": "qri:db:x"}},
                                        "qri:app:sense://a": {"label": "App", "metadata": {"type": "APP", "id": "qri:app:sense://a"}}},
                              "edges": [{"source": "qri:db:x", "target": "qri:app:sense://a", "relation": "LOAD"}]}}
        if path.startswith("/api/v1/knowledgebases/kb1/actions/search"):
            return {"chunks": [{"text": "Refund policy is 30 days", "semanticScore": 0.91, "chunkMeta": {"source": "policy.pdf", "documentId": "d1"}}]}
        if path.startswith("/api/v1/knowledgebases"):
            return {"data": [{"id": "kb1", "name": "Policies"}]}
        if path.startswith("/api/v1/di-projects/p1/bindings"):
            return {"bindings": [{"name": "SRC_CONN", "value": "prod-db"}]}
        if path.startswith("/api/v1/di-projects/p1/di-tasks/task1/runtime/state"):
            return {"state": "RUNNING"}
        if path.startswith("/api/v1/di-projects/p1/di-tasks"):
            return {"data": [{"id": "task1", "name": "Landing", "type": "LANDING", "state": "RUNNING"}]}
        if path.startswith("/api/v1/di-projects"):
            return {"data": [{"id": "p1", "name": "Sales pipeline", "spaceId": "sp1"}]}
        if path.startswith("/api/v1/data-connections"):
            return {"data": [{"id": "dc1", "qName": "Snowflake prod", "datasourceID": "snowflake"}]}
        if path == "/api/v1/data-alerts/actions/trigger":
            return {"workflowID": "wf-1"}
        if path.startswith("/api/v1/data-alerts/al1/executions"):
            return {"data": [{"id": "ex1", "triggerTime": "2026-09-01T00:00:00Z", "conditionStatus": "FINISHED", "executionEvaluationStatus": "CONDITION_MET"}]}
        if path.startswith("/api/v1/data-alerts/al1"):
            return {"id": "al1", "name": "Low stock", "appId": "app-1", "enabled": True, "recipients": {"userIds": []}}
        if path.startswith("/api/v1/data-alerts"):
            return {"data": [{"id": "al1", "name": "Low stock", "appId": "app-1", "enabled": True}]}
        if path.endswith("/realtime-predictions/actions/run"):
            return {"data": {"type": "realtime-prediction", "attributes": {"schema": [{"name": "churn_predicted"}], "rows": [["yes"], ["no"]]}}}
        if path.startswith("/api/v1/ml/experiments/exp1/models"):
            return {"data": [{"id": "m1", "type": "model", "attributes": {"name": "xgb", "algorithm": "xgb_classifier", "status": "ready"}}]}
        if path.startswith("/api/v1/ml/experiments/exp1"):
            return {"data": {"id": "exp1", "type": "experiment", "attributes": {"name": "Churn", "spaceId": "sp1"}}}
        if path.startswith("/api/v1/ml/experiments"):
            return {"data": [{"id": "exp1", "type": "experiment", "attributes": {"name": "Churn"}}]}
        if path.startswith("/api/v1/ml/deployments/dep1"):
            return {"data": {"id": "dep1", "type": "deployment", "attributes": {"name": "Churn v1", "enablePredictions": True}}}
        if path.startswith("/api/v1/ml/deployments"):
            return {"data": [{"id": "dep1", "type": "deployment", "attributes": {"name": "Churn v1"}}]}
        if path.startswith("/api/v1/reloads/rl1/actions/cancel"):
            return {}
        if path.startswith("/api/v1/reloads/rl1"):
            return {"id": "rl1", "appId": "app-1", "status": "SUCCEEDED", "type": "manual"}
        if path == "/api/v1/reloads":
            return {"data": [{"id": "rl1", "appId": "app-1", "status": "SUCCEEDED"}]} if method == "GET" else {"id": "rl2", "appId": body["appId"], "status": "QUEUED"}
        if "/reloads/logs/" in path:
            return {"raw_content": "Started\nLoading Sales\nFinished"}
        if path.endswith("/reloads/logs"):
            return {"data": [{"reloadId": "rl1", "endTime": "2026-09-01T00:00:00Z", "size": 120}]}
        if path.startswith("/api/v1/spaces/sp1"):
            return {"id": "sp1", "name": "Finance", "type": "shared"}
        if path.startswith("/api/v1/spaces"):
            return {"data": [{"id": "sp1", "name": "Finance", "type": "shared"}]}
        if path == "/api/v1/questions/actions/ask":
            return {"conversationalResponse": [{"drillDownURI": "https://t/ia", "responses": [
                {"type": "narrative", "narrative": {"text": "Sales grew 12% in 2025."}},
                {"type": "followup", "followupSentence": "Show sales by region"}]}],
                "nluInfo": {"elements": [{"text": "sales", "typeName": "measure"}]}}
        if path.startswith("/api/v1/apps/") and path.endswith("/data/lineage"):
            return [{"discriminator": "lib://Snowflake", "statement": "LOAD * FROM orders"}]
        if method == "DELETE":
            return None
        return {"id": "generic", "name": "generic"}


def text_like(path: str) -> bool:
    return path.endswith("/actions/export-documentation")


SAMPLE_VALUES = {
    "automation_id": "auto-1", "run_id": "run-1", "glossary_id": "g1", "term_id": "t1", "dataset_id": "ds-1",
    "computation_id": "comp-1", "data_product_id": "dp-1", "knowledgebase_id": "kb1", "project_id": "p1",
    "task_id": "task1", "alert_id": "al1", "experiment_id": "exp1", "deployment_id": "dep1", "reload_id": "rl1",
    "space_id": "sp1", "app_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "connector": "Slack",
    "question": "How did sales do in 2025?", "query": "revenue", "name": "New thing", "columns": ["age", "state"],
    "rows": [["42", "NY"]], "resource_type": "app", "resource_id": "app-1", "status": "verified",
    "enabled": True, "qri": "qri:app:sense://a", "text": "hello",
}


def sample_args(tool) -> dict:
    """Required params always; optional ones only when a realistic sample exists."""
    args = {}
    for param in tool.params:
        if not param.required and param.name in SAMPLE_VALUES:
            args[param.name] = SAMPLE_VALUES[param.name]
        if param.required:
            if param.name in SAMPLE_VALUES:
                args[param.name] = SAMPLE_VALUES[param.name]
            elif param.enum:
                args[param.name] = param.enum[0]
            elif param.type is int:
                args[param.name] = 1
            elif param.type is bool:
                args[param.name] = True
            elif param.type is str:
                args[param.name] = "sample"
            else:
                args[param.name] = []
    return args


def _stack():
    config = Config()
    config.qlik.tenant_url = "https://t.us.qlikcloud.com"
    config.qlik.api_key = "k"
    rest = RecordingRestClient()
    server = create_server(config, qlik_client=rest, engine_client=FakeEngineClient(config))
    return server, rest


async def _call(server, name, args):
    result = await server.call_tool(name, args)
    assert result.is_error is False, result.content
    payload = result.structured_content
    assert "error" not in payload, (name, payload)
    return payload


class TestCatalogShape:
    def test_names_are_unique_and_prefixed(self):
        names = [t.name for t in REST_TOOLS]
        assert len(names) == len(set(names))
        assert all(n.startswith("qlik_") for n in names)

    def test_every_tool_has_group_and_description(self):
        for tool in REST_TOOLS:
            assert tool.group != "rest", tool.name
            assert len(tool.description) > 40, tool.name
            for param in tool.params:
                assert isinstance(param, P) and param.description, (tool.name, param.name)

    def test_path_params_are_declared(self):
        for tool in REST_TOOLS:
            if tool.custom is None:
                for placeholder in set(__import__("re").findall(r"{(\w+)}", tool.path)):
                    assert any(p.name == placeholder and p.where == "path" for p in tool.params), (tool.name, placeholder)


@pytest.mark.parametrize("tool", REST_TOOLS, ids=lambda t: t.name)
async def test_every_rest_tool_runs_against_the_fake_tenant(tool):
    server, rest = _stack()
    payload = await _call(server, tool.name, sample_args(tool))
    assert isinstance(payload, dict) and payload
    for call in rest.calls:
        assert "{" not in call["path"], (tool.name, call["path"])
        assert call["method"] in ("GET", "POST", "PUT", "PATCH", "DELETE", "GET-URL"), tool.name
    if tool.custom is None:
        assert rest.calls[0]["method"] == tool.method
        assert rest.calls[0]["path"].startswith(tool.path.split("{")[0])


class TestRequestBuilding:
    async def test_query_params_are_camel_cased(self):
        server, rest = _stack()
        await _call(server, "qlik_list_automations", {"filter": 'name co "Daily"', "limit": 5, "list_all": True})
        assert rest.calls[0]["params"] == {"filter": 'name co "Daily"', "limit": 5, "listAll": True}

    async def test_explicit_api_names_win(self):
        server, rest = _stack()
        await _call(server, "qlik_list_data_alerts", {"app_id": "app-1"})
        assert rest.calls[0]["params"] == {"appID": "app-1", "limit": 50}

    async def test_json_patch_only_includes_given_fields(self):
        server, rest = _stack()
        await _call(server, "qlik_update_glossary_term", {"glossary_id": "g1", "term_id": "t1", "description": "New def"})
        assert rest.calls[0]["method"] == "PATCH"
        assert rest.calls[0]["json"] == [{"op": "replace", "path": "/description", "value": "New def"}]

    async def test_json_patch_with_nothing_to_change_is_an_error(self):
        server, rest = _stack()
        result = await server.call_tool("qlik_update_glossary_term", {"glossary_id": "g1", "term_id": "t1"})
        assert "Nothing to update" in result.structured_content["error"]

    async def test_glossary_search_builds_scim_filter(self):
        server, rest = _stack()
        await _call(server, "qlik_search_glossary_terms", {"glossary_id": "g1", "query": 'reve"nue', "status": "verified"})
        assert rest.calls[0]["params"]["filter"] == '(name co "revenue" or description co "revenue" or abbreviation co "revenue") and status eq "verified"'

    async def test_path_params_reject_traversal(self):
        server, rest = _stack()
        result = await server.call_tool("qlik_get_automation_by_id", {"automation_id": "../admin"})
        assert "Invalid automation_id" in result.structured_content["error"]
        assert rest.calls == []

    async def test_start_run_sends_api_context(self):
        server, rest = _stack()
        payload = await _call(server, "qlik_start_automation_run", {"automation_id": "auto-1"})
        assert rest.calls[0]["json"] == {"context": "api"}
        assert payload["automation_id"] == "auto-1"

    async def test_term_link_body(self):
        server, rest = _stack()
        await _call(server, "qlik_create_glossary_term_links", {
            "glossary_id": "g1", "term_id": "t1", "resource_type": "app", "resource_id": "app-1",
            "sub_resource_type": "master_measure", "sub_resource_id": "m1", "sub_resource_name": "Revenue",
        })
        assert rest.calls[0]["json"] == {"resourceType": "app", "resourceId": "app-1", "type": "definition",
                                         "subResourceType": "master_measure", "subResourceId": "m1", "subResourceName": "Revenue"}

    async def test_prediction_body_and_shape(self):
        server, rest = _stack()
        payload = await _call(server, "qlik_run_ml_prediction", {
            "deployment_id": "dep1", "columns": ["age", "state"], "rows": [["42", "NY"], ["31", "CA"]], "include_shap": True,
        })
        assert rest.calls[0]["json"] == {"schema": [{"name": "age"}, {"name": "state"}], "rows": [["42", "NY"], ["31", "CA"]]}
        assert rest.calls[0]["params"] == {"includeShap": True}
        assert payload["rows"] == [["yes"], ["no"]]


class TestResponseShaping:
    async def test_dataset_schema_and_sample(self):
        server, rest = _stack()
        schema = await _call(server, "qlik_get_dataset_schema", {"dataset_id": "ds-1"})
        assert schema["fields"][0] == {"name": "Region", "type": "STRING", "nullable": True}
        sample = await _call(server, "qlik_get_dataset_sample", {"dataset_id": "ds-1", "max_rows": 1})
        assert sample["samples"][0]["rows"] == [["East", "100"]]

    async def test_trust_score(self):
        server, rest = _stack()
        payload = await _call(server, "qlik_get_dataset_trust_score", {"dataset_id": "ds-1"})
        assert rest.calls[0]["json"] == {"datasetIds": ["ds-1"]}
        assert payload["score"] == 4.2

    async def test_dataset_memberships_fan_out(self):
        server, rest = _stack()
        payload = await _call(server, "qlik_get_dataset_memberships", {"dataset_id": "ds-1"})
        assert payload["data_products"][0]["id"] == "dp-1"

    async def test_lineage_from_app_id(self):
        server, rest = _stack()
        payload = await _call(server, "qlik_get_lineage", {"app_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"})
        assert rest.calls[0]["path"].startswith("/api/v1/lineage-graphs/nodes/qri%3Aapp%3Asense%3A%2F%2Fa1b2c3d4")
        assert payload["edge_count"] == 1
        assert {n["type"] for n in payload["nodes"]} == {"DATABASE", "APP"}

    async def test_lineage_downstream_uses_impact(self):
        server, rest = _stack()
        await _call(server, "qlik_get_lineage", {"qri": "qri:app:sense://a", "direction": "downstream", "levels": 2})
        assert rest.calls[0]["path"].startswith("/api/v1/lineage-graphs/impact/")
        assert rest.calls[0]["params"] == {"down": 2}

    async def test_fetch_run_waits_until_finished(self):
        server, rest = _stack()
        payload = await _call(server, "qlik_fetch_automation_run", {"automation_id": "auto-1", "run_id": "run-1", "timeout_seconds": 10})
        assert payload["finished"] is True
        assert payload["status"] == "finished"
        assert rest.run_polls == 2

    async def test_run_log_downloads_export_url(self):
        server, rest = _stack()
        payload = await _call(server, "qlik_get_automation_run_log", {"automation_id": "auto-1", "run_id": "run-1"})
        assert rest.calls[-1]["path"] == "https://tenant.us.qlikcloud.com/logs/run-1"
        assert "block 2 failed" in payload["log"]

    async def test_automation_inputs_extracted(self):
        server, rest = _stack()
        payload = await _call(server, "qlik_get_automation_inputs", {"automation_id": "auto-1"})
        assert payload["inputs"][0]["name"] == "region"
        assert "StartBlock" in payload["block_types"]

    async def test_update_automation_merges_current_definition(self):
        server, rest = _stack()
        await _call(server, "qlik_update_automation", {"automation_id": "auto-1", "description": "changed"})
        put = rest.calls[-1]
        assert put["method"] == "PUT"
        assert put["json"]["name"] == "Daily sync"
        assert put["json"]["description"] == "changed"
        assert put["json"]["schedules"] == [{"interval": 86400, "timezone": "UTC", "startAt": "2026-01-01T00:00:00Z"}]

    async def test_documentation_is_text(self):
        server, rest = _stack()
        payload = await _call(server, "qlik_get_data_product_documentation", {"data_product_id": "dp-1"})
        assert rest.calls[0]["text"] is True
        assert payload["markdown"].startswith("# Product docs")

    async def test_ask_question_shape(self):
        server, rest = _stack()
        payload = await _call(server, "qlik_ask_question", {"app_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "question": "How did sales do?"})
        assert rest.calls[0]["json"]["app"] == {"id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"}
        assert payload["responses"][0]["narrative"] == "Sales grew 12% in 2025."
        assert payload["open_in_insight_advisor"] == "https://t/ia"

    async def test_reload_log_tail(self):
        server, rest = _stack()
        payload = await _call(server, "qlik_get_app_reload_log", {"app_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "reload_id": "rl1", "max_chars": 500})
        assert payload["log"].endswith("Finished")
        listing = await _call(server, "qlik_get_app_reload_log", {"app_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"})
        assert listing["logs"][0]["reload_id"] == "rl1"


class TestGating:
    async def test_write_rest_tools_hidden_when_writes_disallowed(self):
        config = Config()
        config.qlik.tenant_url = "https://t.us.qlikcloud.com"
        config.qlik.api_key = "k"
        config.tools.allow_writes = False
        server = create_server(config, qlik_client=RecordingRestClient(), engine_client=FakeEngineClient(config))
        names = {t.name for t in await server.list_tools()}
        assert "qlik_delete_automation" not in names
        assert "qlik_start_reload" not in names
        assert "qlik_list_automations" in names

    async def test_schemas_of_rest_tools_are_flat(self):
        server, _ = _stack()
        for tool in await server.list_tools():
            text = json.dumps(tool.input_schema)
            assert "$ref" not in text and "anyOf" not in text, tool.name
