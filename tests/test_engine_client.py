"""Tests for the Engine API client."""


import pytest

from qlik_mcp_server.engine_client import EngineError, EngineSession, HypercubeResult, _validate_id


class TestAppIdValidation:
    def test_valid_uuid(self):
        result = _validate_id("a1b2c3d4-e5f6-7890-abcd-ef1234567890", "app_id")
        assert result == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

    def test_valid_uuid_uppercase(self):
        result = _validate_id("A1B2C3D4-E5F6-7890-ABCD-EF1234567890", "app_id")
        assert result == "A1B2C3D4-E5F6-7890-ABCD-EF1234567890"

    def test_rejects_empty(self):
        with pytest.raises(EngineError, match="Invalid app_id"):
            _validate_id("", "app_id")

    def test_rejects_path_traversal(self):
        with pytest.raises(EngineError, match="Invalid app_id"):
            _validate_id("../../etc/passwd", "app_id")

    def test_rejects_url_injection(self):
        with pytest.raises(EngineError, match="Invalid app_id"):
            _validate_id("evil.com/steal?data=1", "app_id")

    def test_rejects_non_uuid_string(self):
        with pytest.raises(EngineError, match="Invalid app_id"):
            _validate_id("my-app-name", "app_id")


class TestHypercubeResult:
    def test_to_table_format(self):
        result = HypercubeResult(
            headers=["Region", "Revenue"],
            rows=[
                ["East", "1000"],
                ["West", "2000"],
                ["North", "1500"],
            ],
            total_rows=3,
            truncated=False,
        )

        table = result.to_table()
        assert "Region" in table
        assert "Revenue" in table
        assert "East" in table
        assert "2000" in table

    def test_to_table_empty(self):
        result = HypercubeResult()
        assert result.to_table() == "(no data)"

    def test_to_table_truncated(self):
        result = HypercubeResult(
            headers=["A"],
            rows=[["1"]],
            total_rows=100,
            truncated=True,
        )
        table = result.to_table()
        assert "truncated" in table
        assert "100" in table

    def test_to_records(self):
        result = HypercubeResult(
            headers=["Name", "Value"],
            rows=[
                ["Alice", "100"],
                ["Bob", "200"],
            ],
            total_rows=2,
        )

        records = result.to_records()
        assert len(records) == 2
        assert records[0] == {"Name": "Alice", "Value": "100"}
        assert records[1] == {"Name": "Bob", "Value": "200"}

    def test_to_records_empty(self):
        result = HypercubeResult()
        assert result.to_records() == []


class TestEngineSessionStaticMethods:
    def test_extract_cells(self):
        layout = {
            "cells": [
                {"name": "obj1", "type": "barchart", "col": 0, "row": 0, "colspan": 6, "rowspan": 4},
                {"name": "obj2", "type": "kpi", "col": 6, "row": 0, "colspan": 6, "rowspan": 2},
            ]
        }

        cells = EngineSession._extract_cells(layout)
        assert len(cells) == 2
        assert cells[0]["name"] == "obj1"
        assert cells[0]["type"] == "barchart"
        assert cells[0]["bounds"]["width"] == 6

    def test_extract_cells_empty(self):
        assert EngineSession._extract_cells({}) == []

    def test_build_child_props(self):
        obj_def = {
            "type": "barchart",
            "title": "Revenue by Region",
            "dimensions": ["Region"],
            "measures": ["Sum(Revenue)"],
        }

        props = EngineSession._build_child_props(obj_def, row=0)
        assert props["qInfo"]["qType"] == "barchart"
        assert len(props["qHyperCubeDef"]["qDimensions"]) == 1
        assert len(props["qHyperCubeDef"]["qMeasures"]) == 1
        assert props["qHyperCubeDef"]["qDimensions"][0]["qDef"]["qFieldDefs"] == ["Region"]
