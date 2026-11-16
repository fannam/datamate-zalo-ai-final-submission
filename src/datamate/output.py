from __future__ import annotations

import datetime as dt
from decimal import Decimal
import math

from .parsing import parse_datetime, round_decimal
from .types import SQLPlan


class InvalidResult(ValueError):
    pass


def finalize(plan: SQLPlan, columns: list[str], rows: list[tuple]):
    if plan.answer_type in ("abstain", "unanswerable"):
        return {"type": plan.answer_type, **({"reason": plan.reason} if plan.answer_type == "unanswerable" else {})}
    shape=plan.shape
    # Recover only unambiguous representation mismatches; never drop columns.
    if plan.answer_type=='table':
        shape='table'
    elif plan.answer_type=='list':
        if len(columns)==1:
            shape='column'
        elif len(rows)==1:
            shape='row'
    if shape == "scalar":
        if len(rows) != 1 or len(rows[0]) != 1:
            raise InvalidResult("scalar output requires exactly one row and one column")
        answer = rows[0][0]
    elif shape == "column":
        if any(len(row) != 1 for row in rows):
            raise InvalidResult("column list requires exactly one column")
        answer = [row[0] for row in rows]
    elif shape == "row":
        if len(rows) != 1:
            raise InvalidResult("row list requires exactly one row")
        answer = list(rows[0])
    else:
        answer = {"columns": columns, "rows": [list(row) for row in rows]}
    kind = plan.answer_type
    if kind in ("number", "percent"):
        if isinstance(answer, bool) or not isinstance(answer, (int, float, Decimal)):
            raise InvalidResult("numeric answer must be a finite number; NULL is not automatically zero")
        places = 2 if kind == "percent" or plan.rounding in ("percent", "decimal2") else (0 if plan.rounding in ("money", "integer") else None)
        answer = convert_number(answer, places)
    elif kind == "boolean":
        if type(answer) is not bool:
            raise InvalidResult("boolean query must return a SQL boolean")
    elif kind == "date":
        value = parse_datetime(answer)
        if value is None and isinstance(answer, str) and len(answer) == 7:
            value = parse_datetime(answer + "-01")
        if value is None:
            raise InvalidResult("date output cannot be parsed")
        answer = value.strftime("%Y-%m" if plan.date_granularity == "month" else "%Y-%m-%d")
    elif kind == "string":
        if not isinstance(answer, str):
            raise InvalidResult("string answer must be a string")
    elif kind in ("list", "table"):
        if kind == "list" and not isinstance(answer, list) or kind == "table" and not isinstance(answer, dict):
            raise InvalidResult("list/table shape mismatch")
        answer = convert_nested(answer, 0 if plan.rounding in ("money", "integer") else 2 if plan.rounding in ("percent", "decimal2") else None)
    return {"type": kind, "answer": answer}


def suspicious(answer):
    """Answer-level sanity checks; a flagged answer is abstained rather than risked.

    Exact zero is almost never a correct business answer in released train data
    (4 of 3000); it usually means a filter matched nothing or the source does not
    record the quantity. Abstaining costs 0, a wrong answer costs 1.
    """
    kind, value = answer.get("type"), answer.get("answer")
    if kind in ("number", "percent") and value == 0:
        return "zero_result"
    if kind == "list" and value and all(isinstance(item, (int, float)) and not isinstance(item, bool) and item == 0 for item in value):
        return "all_zero_list"
    if kind == "list" and not value:
        return "empty_list"
    return None


def convert_number(value, places=None):
    decimal = Decimal(str(value))
    if not decimal.is_finite():
        raise InvalidResult("non-finite numeric result")
    if places is not None:
        decimal = round_decimal(decimal, places)
    return int(decimal) if decimal == decimal.to_integral() else float(decimal)


def convert_nested(value, places):
    if isinstance(value, dict):
        return {key: convert_nested(item, places) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [convert_nested(item, places) for item in value]
    if isinstance(value, (Decimal, float, int)) and not isinstance(value, bool):
        return convert_number(value, places)
    if isinstance(value, (dt.datetime, dt.date)):
        return value.strftime("%Y-%m-%d")
    return value
