"""Tests for MCP tool input validation."""

import pytest
from pydantic import ValidationError

from qlik_mcp_server.tools.get_sheet_details import GetSheetDetailsInput
from qlik_mcp_server.tools.get_hypercube_data import GetHypercubeDataInput, Filter
from qlik_mcp_server.tools.create_sheet import CreateSheetInput, VisualizationObject
from qlik_mcp_server.tools.search import SearchInput


class TestGetSheetDetailsInput:
    def test_valid_with_app_id_only(self):
        inp = GetSheetDetailsInput(app_id="abc-123")
        assert inp.app_id == "abc-123"
        assert inp.sheet_id is None

    def test_valid_with_sheet_id(self):
        inp = GetSheetDetailsInput(app_id="abc-123", sheet_id="sheet-1")
        assert inp.sheet_id == "sheet-1"

    def test_missing_app_id(self):
        with pytest.raises(ValidationError):
            GetSheetDetailsInput()


class TestGetHypercubeDataInput:
    def test_valid_basic(self):
        inp = GetHypercubeDataInput(
            app_id="app1",
            dimensions=["Region"],
            measures=["Sum(Revenue)"],
        )
        assert inp.dimensions == ["Region"]
        assert inp.measures == ["Sum(Revenue)"]
        assert inp.filters is None
        assert inp.max_rows == 1000

    def test_with_filters(self):
        inp = GetHypercubeDataInput(
            app_id="app1",
            dimensions=["Product"],
            measures=["Count(OrderID)"],
            filters=[
                Filter(field="Year", values=["2025"]),
                Filter(field="Region", values=["East", "West"]),
            ],
        )
        assert len(inp.filters) == 2
        assert inp.filters[0].field == "Year"
        assert inp.filters[1].values == ["East", "West"]

    def test_missing_dimensions(self):
        with pytest.raises(ValidationError):
            GetHypercubeDataInput(app_id="app1", measures=["Sum(X)"])

    def test_missing_measures(self):
        with pytest.raises(ValidationError):
            GetHypercubeDataInput(app_id="app1", dimensions=["X"])

    def test_empty_dimensions_rejected(self):
        with pytest.raises(ValidationError):
            GetHypercubeDataInput(app_id="app1", dimensions=[], measures=["Sum(X)"])

    def test_empty_measures_rejected(self):
        with pytest.raises(ValidationError):
            GetHypercubeDataInput(app_id="app1", dimensions=["X"], measures=[])

    def test_empty_filter_field_rejected(self):
        with pytest.raises(ValidationError):
            Filter(field="", values=["val"])

    def test_empty_filter_values_rejected(self):
        with pytest.raises(ValidationError):
            Filter(field="Year", values=[])


class TestCreateSheetInput:
    def test_valid_basic(self):
        inp = CreateSheetInput(
            app_id="app1",
            title="Revenue Analysis",
        )
        assert inp.title == "Revenue Analysis"
        assert inp.objects == []

    def test_with_objects(self):
        inp = CreateSheetInput(
            app_id="app1",
            title="Dashboard",
            objects=[
                VisualizationObject(
                    type="barchart",
                    title="Revenue by Region",
                    dimensions=["Region"],
                    measures=["Sum(Revenue)"],
                ),
                VisualizationObject(
                    type="kpi",
                    title="Total Revenue",
                    measures=["Sum(Revenue)"],
                ),
            ],
        )
        assert len(inp.objects) == 2
        assert inp.objects[0].type == "barchart"
        assert inp.objects[1].dimensions == []

    def test_missing_title(self):
        with pytest.raises(ValidationError):
            CreateSheetInput(app_id="app1")


class TestSearchInput:
    def test_valid_basic(self):
        inp = SearchInput(query="revenue dashboard")
        assert inp.query == "revenue dashboard"
        assert inp.resource_type is None
        assert inp.limit == 20

    def test_with_filters(self):
        inp = SearchInput(
            query="HR",
            resource_type="app",
            space="space-123",
            limit=10,
        )
        assert inp.resource_type == "app"
        assert inp.space == "space-123"
        assert inp.limit == 10

    def test_missing_query(self):
        with pytest.raises(ValidationError):
            SearchInput()

    def test_invalid_resource_type(self):
        with pytest.raises(ValidationError):
            SearchInput(query="test", resource_type="invalid_type")

    def test_valid_resource_types(self):
        for rt in ("app", "dataset", "automation", "note"):
            inp = SearchInput(query="test", resource_type=rt)
            assert inp.resource_type == rt


class TestSearchResourceTypes:
    def test_data_product_and_qvapp_accepted(self):
        for rt in ("dataproduct", "qvapp", "collection"):
            assert SearchInput(query="x", resource_type=rt).resource_type == rt


class TestCreateSheetVisTypes:
    def test_non_hypercube_types_rejected(self):
        from qlik_mcp_server.tools.create_sheet import _ALLOWED_VIS_TYPES
        assert "filterpane" not in _ALLOWED_VIS_TYPES
        assert "barchart" in _ALLOWED_VIS_TYPES
