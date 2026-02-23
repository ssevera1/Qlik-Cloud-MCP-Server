"""qlik_search — Discover apps and data products across the catalog.

Enables the agent to traverse the entire catalog of apps and data
products to find relevant assets, facilitating "Metric Discovery"
across the enterprise.
"""

from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from ..qlik_cloud_client import QlikCloudClient, QlikCloudError

logger = logging.getLogger(__name__)

_VALID_RESOURCE_TYPES = frozenset({
    "app", "dataset", "automation", "note", "dataconnection",
    "genericlink", "sharingservicetask", "insight",
})


class SearchInput(BaseModel):
    """Input schema for qlik_search."""

    query: str = Field(
        description=(
            "Search text to find apps, data products, or other resources. "
            "Matches against name, description, and tags. "
            "Example: 'revenue dashboard', 'HR attrition', 'sales forecast'"
        )
    )
    resource_type: Optional[str] = Field(
        default=None,
        description=(
            "Filter results by resource type: 'app', 'dataset', "
            "'automation', 'note', or omit for all types"
        )
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
    space: Optional[str] = Field(
        default=None,
        description="Filter by space ID to search within a specific space"
    )
    limit: Optional[int] = Field(
        default=20,
        ge=1,
        description="Maximum number of results to return (default: 20, max: 40)"
    )


TOOL_DESCRIPTION = (
    "Search the Qlik Cloud catalog to find apps, datasets, automations, and other resources. "
    "Use this for metric discovery — finding which apps contain relevant data before "
    "requesting specific data with qlik_get_hypercube_data. "
    "Returns a list of matching resources with their IDs, names, types, and descriptions."
)


async def handle_search(
    client: QlikCloudClient, params: dict
) -> dict:
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
