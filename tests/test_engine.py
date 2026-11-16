from pathlib import Path

import duckdb
import pytest

from datamate.engine import QueryEngine, UnsafeQuery, validate_sql


@pytest.mark.parametrize("sql", [
    "SELECT * FROM read_csv('/tmp/secret')", "SELECT read_text('/tmp/secret')", "COPY sales TO '/tmp/out'",
    "SELECT * FROM other_db.main.sales", "SELECT * FROM answers", "SELECT 1; SELECT 2", "INSTALL httpfs",
    "SELECT * FROM query('SELECT * FROM sales')", "SELECT * FROM duckdb_secrets()",
    "WITH RECURSIVE x AS (SELECT 1 UNION ALL SELECT * FROM x) SELECT * FROM x",
])
def test_sql_guard(sql):
    with pytest.raises(UnsafeQuery):
        validate_sql(sql, {"sales"})


def test_local_query_and_whole_returned_order_exclusion(tmp_path):
    path = tmp_path / "data.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE sales(id VARCHAR, day DATE, amount INTEGER, status VARCHAR)")
    con.execute("INSERT INTO sales VALUES ('a','2026-01-01',100,'Xong'),('a','2026-02-01',-20,'trả hàng'),('b','2026-01-02',50,'Đang giao')")
    con.close()
    engine = QueryEngine(path, {"sales"})
    try:
        columns, rows = engine.query("SELECT SUM(amount) AS revenue FROM sales WHERE day BETWEEN DATE '2026-01-01' AND DATE '2026-01-31' AND id NOT IN (SELECT id FROM sales WHERE stat(status) IN ('cancelled','returned'))")
        assert rows == [(50,)]
        assert engine.query("SELECT norm('CÀ PHÊ'), is_guest('Khách lẻ'), pay_method('COD')")[1] == [("ca phe", True, "cod")]
        with pytest.raises(UnsafeQuery):
            engine.query("SELECT * FROM read_csv('/etc/passwd')")
    finally:
        engine.close()
