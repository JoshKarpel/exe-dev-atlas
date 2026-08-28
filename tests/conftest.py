from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from without_http import ConnectionPool

# High enough that nothing holds it, so every read about it comes back empty and the row
# carries the blanks a listener whose process could not be read renders with.
ABSENT_PID = 4_194_303


@pytest.fixture
async def pool() -> AsyncIterator[ConnectionPool]:
    """An open connection pool, torn down with the test that asked for one."""
    async with ConnectionPool() as open_pool:
        yield open_pool
