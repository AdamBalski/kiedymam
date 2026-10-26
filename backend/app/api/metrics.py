from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.admin import admin_user
from app.api.auth import require_csrf
from app.metrics.query import ArithmeticOperation as ArithmeticOperationSpec
from app.metrics.query import Delta as DeltaSpec
from app.metrics.query import MetricOperation as MetricOperationSpec
from app.metrics.query import Predicate as PredicateSpec
from app.metrics.query import QuerySpec
from app.metrics.query import execute_query

router = APIRouter(prefix="/api/admin/metrics", tags=["admin metrics"])


class MetricOperation(BaseModel):
    type: Literal["metric"]
    operation: str = Field(min_length=1, max_length=8)
    group_by: list[str] | None = Field(default=None, max_length=8)
    interval_seconds: int | None = Field(
        default=None, ge=1,
        description="Aggregate the whole requested range when omitted; otherwise produce time buckets of this size.",
    )


class Predicate(BaseModel):
    """Compare each current numeric series value with a scalar threshold."""

    type: Literal["predicate"]
    operator: Literal[">", ">=", "<", "<=", "=", "!="] = Field(
        description="Comparison applied to the current metric value.",
    )
    value: float = Field(description="Numeric literal compared with each current metric value.")


class DeltaOperation(BaseModel):
    """Replace each series with differences between adjacent samples."""

    type: Literal["delta"]


class ArithmeticOperation(BaseModel):
    type: Literal["arithmetic"]
    operator: Literal["add", "subtract", "multiply", "divide"]
    query: QueryRequest


Operation = Annotated[
    MetricOperation | Predicate | DeltaOperation | ArithmeticOperation,
    Field(discriminator="type"),
]


class QueryRequest(BaseModel):
    metric: str = Field(min_length=1, max_length=160)
    start: float
    end: float
    filters: dict[str, str | int | float | bool] = Field(default_factory=dict)
    operations: list[Operation] = Field(min_length=1, max_length=16)


ArithmeticOperation.model_rebuild()


@router.get("/catalog")
async def metric_catalog(request: Request, _: dict = Depends(admin_user)) -> dict:
    store = request.app.state.metrics
    return {
        "service": {"name": store.service_name, "version": store.service_version},
        "metrics": store.catalog(),
    }


@router.post("/query", dependencies=[Depends(require_csrf)])
async def query_metrics(payload: QueryRequest, request: Request, _: dict = Depends(admin_user)) -> dict:
    try:
        query = _query_spec(payload, request.app.state.metrics)
        result = execute_query(request.app.state.metrics, query)
        return {
            "metric": payload.metric,
            "type": request.app.state.metrics.metric_type(query.metric),
            "start": int(payload.start),
            "end": int(payload.end),
            "operations": _serialize_operations(query.operations),
            "step_seconds": result["step_seconds"],
            "series": result["series"],
            "service": {"name": request.app.state.metrics.service_name,
                        "version": request.app.state.metrics.service_version},
        }
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _query_spec(payload: QueryRequest, store, depth: int = 0) -> QuerySpec:
    if depth > 4:
        raise ValueError("Arithmetic query nesting is limited to four levels")
    store.metric_type(payload.metric)
    operations = []
    for operation in payload.operations:
        if isinstance(operation, MetricOperation):
            operations.append(MetricOperationSpec(
                operation=operation.operation,
                group_by=tuple(operation.group_by or []),
                interval_seconds=operation.interval_seconds,
            ))
        elif isinstance(operation, Predicate):
            operations.append(PredicateSpec(operator=operation.operator, value=operation.value))
        elif isinstance(operation, DeltaOperation):
            operations.append(DeltaSpec())
        else:
            operations.append(ArithmeticOperationSpec(
                operator=operation.operator,
                query=_query_spec(operation.query, store, depth + 1),
            ))
    return QuerySpec(
        metric=payload.metric,
        start=int(payload.start), end=int(payload.end),
        filters=payload.filters, operations=tuple(operations),
    )


def _serialize_operations(operations: tuple) -> list[dict[str, Any]]:
    result = []
    for operation in operations:
        if isinstance(operation, MetricOperationSpec):
            result.append({"type": "metric", "operation": operation.operation,
                           "group_by": list(operation.group_by), "interval_seconds": operation.interval_seconds})
        elif isinstance(operation, PredicateSpec):
            result.append({"type": "predicate", "operator": operation.operator, "value": operation.value})
        elif isinstance(operation, DeltaSpec):
            result.append({"type": "delta"})
        else:
            query = operation.query
            result.append({
                "type": "arithmetic", "operator": operation.operator,
                "query": {"metric": query.metric, "start": query.start, "end": query.end,
                          "filters": query.filters, "operations": _serialize_operations(query.operations)},
            })
    return result
