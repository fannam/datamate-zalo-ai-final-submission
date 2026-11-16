from __future__ import annotations

from pathlib import Path
import threading
from functools import lru_cache
import re

import duckdb
import sqlglot
from sqlglot import exp

from .parsing import channel, fold, guest, parse_datetime, parse_number, payment_method, status


@lru_cache(maxsize=1 << 20)
def norm_text(value):
    """SQL norm(): folded, whitespace-collapsed text. Cached: called once per row on repetitive values."""
    return re.sub(r"\s+", " ", fold(value)).strip()


class UnsafeQuery(ValueError):
    pass


def validate_sql(sql: str, allowed_tables: set[str]):
    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except sqlglot.errors.ParseError as error:
        raise UnsafeQuery("Invalid SQL syntax: " + str(error)) from error
    if len(statements) != 1 or not isinstance(statements[0], exp.Query):
        raise UnsafeQuery("Only one SELECT/CTE query is allowed")
    tree = statements[0]
    if any(type(node).__name__ in {"Insert", "Update", "Delete", "Create", "Drop", "Merge", "Copy", "Attach", "Pragma", "Command", "Set", "Use"} for node in tree.walk()):
        raise UnsafeQuery("Mutation/configuration statements are not allowed")
    ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for with_clause in tree.find_all(exp.With):
        if with_clause.args.get("recursive"):
            raise UnsafeQuery("Recursive SQL is not allowed")
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier) or table.db or table.catalog:
            raise UnsafeQuery("External sources, table functions and qualified catalogs are forbidden")
        if table.name.lower() not in allowed_tables | ctes:
            raise UnsafeQuery(f"Table is not in the business catalog: {table.name}")
    forbidden = ("read_", "write_", "http", "sqlite_", "postgres_", "mysql_", "duckdb_", "parquet_", "pragma_", "arrow_", "getenv", "current_setting", "set_config", "query", "glob", "load_extension", "nextval", "setval")
    for node in tree.find_all(exp.Func):
        name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
        if any(name.startswith(prefix) for prefix in forbidden):
            raise UnsafeQuery(f"Function is not permitted: {name}")
    return tree


class QueryEngine:
    def __init__(self, database: Path, tables: set[str], timeout: float = 15):
        self.tables = {x.lower() for x in tables} | {"calendar", "unit_conversions"}
        self.timeout = timeout
        self.connection = duckdb.connect(str(database), read_only=True, config={"threads": 2, "memory_limit": "1GB"})
        self.connection.execute("SET enable_external_access=false")
        self.connection.execute("SET autoinstall_known_extensions=false")
        self.connection.execute("SET autoload_known_extensions=false")
        self.connection.create_function("norm", norm_text, ["VARCHAR"], "VARCHAR", null_handling="special")
        self.connection.create_function("stat", status, ["VARCHAR"], "VARCHAR", null_handling="special")
        self.connection.create_function("is_guest", guest, ["VARCHAR"], "BOOLEAN", null_handling="special")
        self.connection.create_function("pay_method", payment_method, ["VARCHAR"], "VARCHAR", null_handling="special")
        self.connection.create_function("norm_channel", channel, ["VARCHAR"], "VARCHAR", null_handling="special")
        self.connection.create_function("to_num", parse_number, ["VARCHAR"], "DECIMAL(28,6)", null_handling="special")
        self.connection.create_function("to_day", lambda value: (parsed.date() if (parsed := parse_datetime(value)) else None), ["VARCHAR"], "DATE", null_handling="special")
        self.lock = threading.Lock()

    def query(self, sql: str, max_rows: int = 1000):
        validate_sql(sql, self.tables)
        with self.lock:
            timer = threading.Timer(self.timeout, self.connection.interrupt)
            timer.daemon = True
            timer.start()
            try:
                cursor = self.connection.execute(sql)
                columns = [item[0] for item in cursor.description]
                rows = cursor.fetchmany(max_rows + 1)
                if len(rows) > max_rows:
                    raise ValueError(f"Result exceeds {max_rows} rows; aggregate/filter first (never finalize a truncated result)")
                return columns, rows
            finally:
                timer.cancel()

    def lookup(self, sql: str, max_rows: int = 500000):
        """Internal reads by compilers (names, keys, coverage); answers keep `query`'s small cap."""
        return self.query(sql, max_rows)

    def close(self):
        with self.lock:
            self.connection.close()
