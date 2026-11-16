from __future__ import annotations

from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


AnswerType = Literal["number", "percent", "date", "boolean", "string", "list", "table", "unanswerable", "abstain"]


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    business_id: str
    question: str
    as_of_date: date


class SQLPlan(BaseModel):
    """Explicit execution contract; no free-form final numeric answers."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["query", "inspect", "unanswerable", "abstain"]
    sql: str = Field(max_length=6000, description="One DuckDB SELECT/CTE; no duplicate query, no Markdown, empty for abstain/unanswerable")
    answer_type: AnswerType
    shape: Literal["scalar", "column", "row", "table"]
    rounding: Literal["money", "percent", "integer", "decimal2", "none"]
    date_granularity: Literal["day", "month"]
    reason: str = Field(max_length=300, description="One short evidence sentence, never a guessed answer")


class QueryFrame(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric: Literal["revenue", "orders", "quantity", "gross_profit", "cancelled_orders", "cancel_rate", "purchase_days", "product_order_count", "other"]
    operation: Literal["value", "average_order", "daily_average", "top", "share", "growth", "difference", "series", "trend", "threshold_count", "threshold_share", "other"]
    group_by: Literal["none", "product", "product_group", "customer", "employee", "day", "month", "hour", "payment", "channel"]
    product_filter: str
    customer_filter: str
    employee_filter: str = ''
    group_filter: str
    customer_group_filter: str
    unit_filter: str
    payment_filter: str
    channel_filter: str
    size_filter: str
    k: int = Field(ge=1, le=100)
    threshold: float
    threshold_operator: Literal[">", ">=", "<", "<=", "="]
    weekday_filter: int = Field(ge=0, le=7)
    output_type: AnswerType
    reason: str = Field(max_length=200)

    # The prompt defines these defaults ("k=1 if no top-N is stated", "operator >= by default"); a model that writes
    # 0 or an empty string means exactly that, so reading it that way beats discarding a correct frame.
    @field_validator("k", mode="before")
    @classmethod
    def _default_k(cls, value):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return min(max(int(value), 1), 100)
        return value

    @field_validator("threshold_operator", mode="before")
    @classmethod
    def _default_operator(cls, value):
        return ">=" if isinstance(value, str) and not value.strip() else value
