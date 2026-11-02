from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import settings

pool = ConnectionPool(
    conninfo=settings.database_url,
    kwargs={"row_factory": dict_row},
    min_size=1,
    max_size=10,
    open=False,
)


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    with pool.connection() as conn:
        yield conn
