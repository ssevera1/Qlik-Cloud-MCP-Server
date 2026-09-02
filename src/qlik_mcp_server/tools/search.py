"""qlik_search: discover apps and data products across the catalog.

Enables the agent to traverse the catalog of apps, datasets, and data
products to find relevant assets ("metric discovery") across the tenant.
Reference: https://qlik.dev/apis/rest/items/
"""

from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from ..qlik_cloud_client import QlikCloudClient, QlikCloudError
from .spec import ToolSpec

logger = logging.getLogger(__name__)

# resourceType values accepted by GET /api/v1/items (case-sensitive).
_VALID_RESOURCE_TYPES = frozenset({
    "app", "qvapp", "qlikview", "collection", "insight", "genericlink",
    "sharingservicetask", "note", "dataasset", "dataset", "dataproduct",
    "automation", "automl-experiment", "automl-deployment", "knowledgebase",
    "assistant", "dataflow", "script", "glossary", "dcaas",
})


class SearchInput(BaseModel):
    """Input schema for qlik_search."""

    query: str = Field(
        description=(
            "Search text to find apps, datasets, data products, or other resources. "
            "Case-insensitive match against name and description. "
            "Example: 'revenue dashboard', 'HR attrition', 'sales forecast'"
        ),
        min_length=1,
        max_length=512,
    )
    resource_type: Optional[str] = Field(
        default=None,
        description=(
            "Filter results by resource type: 'app', 'dataset', 'dataproduct', "
            "'automation', 'note', 'glossary', 'knowledgebase', or omit for all types"
        ),
    )
    space: Optional[str] = Field(
        default=None,
        description="Filter by space ID to search within a specific space",
    )
    limit: Optional[int] = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum number of results to return (default: 20, max: 100)",
    )

    @field_validator("resource_type")
    @classmethod
    def validate_resource_type(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in _VALID_RESOURCE_TYPES:
            raise ValueError(
                f"Invalid resource_type '{v}'. "
                f"Allowed: {', '.join(sorted(_VALID_RESOURCE_TYPES))}"
            )
        return v


TOOL_DESCRIPTION = (
    "Search the Qlik Cloud catalog to find apps, datasets, data products, automations, "
    "and other resources. Use this for metric discovery: finding which apps contain "
    "relevant data before requesting specific data with qlik_get_hypercube_data. "
    "Returns matching resources with their IDs, names, types, descriptions, and links. "
    "For apps, use resource_id as the app_id in other tools."
)


async def handle_search(client: QlikCloudClient, params: dict) -> dict:
    """Execute the qlik_search tool."""
    input_data = SearchInput(**params)

    try:
        items = await client.search_items(
            query=input_data.query,
            resource_type=input_data.resource_type,
            space_id=input_data.space,
            limit=input_data.limit or 20,
        )

        return {
            "query": input_data.query,
            "result_count": len(items),
            "results": items,
            "filters": {
                "resource_type": input_data.resource_type,
                "space": input_data.space,
            },
        }

    except QlikCloudError as e:
        logger.error("Search error: %s", e)
        return {
            "error": str(e),
            "query": input_data.query,
            "hint": "Verify the tenant URL and API credentials are correct.",
        }


SEARCH_SPEC = ToolSpec(
    name="qlik_search",
    title="Search Qlik catalog",
    description=TOOL_DESCRIPTION,
    input_model=SearchInput,
    run=lambda ctx, params: handle_search(ctx.qlik_client, params),
)
