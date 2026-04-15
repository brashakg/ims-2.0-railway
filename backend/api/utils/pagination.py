"""
IMS 2.0 - Standardized Pagination
===================================
All list endpoints should use paginate() to return a consistent envelope:

    {
        "data": [...],
        "pagination": {
            "total": 142,
            "page": 1,
            "page_size": 50,
            "total_pages": 3
        }
    }

Usage in a router:
    from api.utils.pagination import paginate, PaginationParams

    @router.get("/items")
    async def list_items(
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=100),
    ):
        items = repo.find_many({})
        return paginate(items, page=page, page_size=page_size)
"""

import math
from typing import Any, List


def paginate(
    items: List[Any],
    *,
    page: int = 1,
    page_size: int = 50,
    total: int = None,
) -> dict:
    """
    Wrap a list of items in the standard pagination envelope.

    If `total` is not provided, len(items) is used (assumes all items
    were fetched). For large collections, pass the true total from a
    count query and slice items yourself before calling this.
    """
    if total is None:
        total = len(items)
    total_pages = max(1, math.ceil(total / page_size))
    return {
        "data": items,
        "pagination": {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        },
    }
