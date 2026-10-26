"""Composable metric query operations shared by API consumers."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any


@dataclass(frozen=True)
class MetricOperation:
    operation: str
    group_by: tuple[str, ...] = ()
    interval_seconds: int | None = None


@dataclass(frozen=True)
class Predicate:
    operator: str
    value: float


@dataclass(frozen=True)
class Delta:
    pass


@dataclass(frozen=True)
class QuerySpec:
    metric: str
    start: int
    end: int
    filters: dict[str, Any]
    operations: tuple["Operation", ...]


@dataclass(frozen=True)
class ArithmeticOperation:
    operator: str
    query: QuerySpec


Operation = MetricOperation | Predicate | Delta | ArithmeticOperation

_COMPARATORS = {
    ">": lambda left, right: left > right,
    ">=": lambda left, right: left >= right,
    "<": lambda left, right: left < right,
    "<=": lambda left, right: left <= right,
    "=": lambda left, right: left == right,
    "!=": lambda left, right: left != right,
}
_BASIC_METRIC_OPERATIONS = {"count", "sum", "mean", "min", "max"}


def execute_query(store, query: QuerySpec) -> dict[str, Any]:
    if not query.operations or not isinstance(query.operations[0], MetricOperation):
        raise ValueError("A query must start with a metric operation")
    first = query.operations[0]
    available_dimensions = {dimension["name"] for dimension in store.dimensions(query.metric)}
    discarded = set(first.group_by) - available_dimensions
    if discarded:
        raise ValueError(f"Unknown dimensions for metric {query.metric!r}: {', '.join(sorted(discarded))}")
    result = store.query(
        query.metric, query.start, query.end,
        filters=query.filters or {}, group_by=list(first.group_by),
        operation=first.operation, interval_seconds=first.interval_seconds,
    )
    series = result["series"]
    step_seconds = result["step_seconds"]
    available_dimensions = set(first.group_by)
    for operation in query.operations[1:]:
        if isinstance(operation, Predicate):
            series = _apply_predicate(series, operation)
        elif isinstance(operation, Delta):
            series = _apply_delta(series)
        elif isinstance(operation, ArithmeticOperation):
            other = execute_query(store, operation.query)
            if query.start != operation.query.start or query.end != operation.query.end:
                raise ValueError("Arithmetic queries must use the same time range")
            if step_seconds != other["step_seconds"]:
                raise ValueError("Arithmetic queries must use the same interval resolution")
            series = _apply_arithmetic(series, other["series"], operation.operator)
        else:
            discarded = set(operation.group_by) - available_dimensions
            if discarded:
                raise ValueError(
                    "Metric operation cannot group by dimensions no longer present in its input: "
                    + ", ".join(sorted(discarded))
                )
            series, step_seconds = _apply_metric_operation(
                series, operation, step_seconds, query.start, query.end,
            )
            available_dimensions = set(operation.group_by)
    return {"series": series, "step_seconds": step_seconds}


def validate_operation(operation: str, kind: str, *, initial: bool) -> None:
    if operation in _BASIC_METRIC_OPERATIONS:
        return
    percentile = re.fullmatch(r"p(100|[1-9]?\d)", operation)
    if percentile and kind == "histogram" and initial:
        return
    if percentile and not initial:
        return
    raise ValueError(f"Operation {operation!r} is not supported for {kind} metrics")


def _apply_predicate(series: list[dict[str, Any]], predicate: Predicate) -> list[dict[str, Any]]:
    if predicate.operator not in _COMPARATORS:
        raise ValueError(f"Unsupported predicate operator: {predicate.operator}")
    if not math.isfinite(predicate.value):
        raise ValueError("Predicate value must be finite")
    compare = _COMPARATORS[predicate.operator]
    return [
        {
            "dimensions": item["dimensions"],
            "points": [
                {"timestamp": point["timestamp"], "value": 1.0 if compare(float(point["value"]), predicate.value) else 0.0}
                for point in item["points"]
            ],
        }
        for item in series
    ]


def _apply_delta(series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace each signal with adjacent-sample differences; each output uses the newer timestamp."""
    result = []
    for item in series:
        points = sorted(item["points"], key=lambda point: point["timestamp"])
        deltas = [
            {
                "timestamp": current["timestamp"],
                "value": float(current["value"]) - float(previous["value"]),
            }
            for previous, current in zip(points, points[1:])
        ]
        result.append({"dimensions": item["dimensions"], "points": deltas})
    return result


def _apply_arithmetic(
    left_series: list[dict[str, Any]], right_series: list[dict[str, Any]], operator: str,
) -> list[dict[str, Any]]:
    if operator not in {"add", "subtract", "multiply", "divide"}:
        raise ValueError(f"Unsupported arithmetic operator: {operator}")
    left_groupings = {tuple(sorted(item["dimensions"].keys())) for item in left_series}
    right_groupings = {tuple(sorted(item["dimensions"].keys())) for item in right_series}
    if left_groupings and right_groupings and left_groupings != right_groupings:
        raise ValueError("Arithmetic queries must group by the same dimensions")
    left = {
        (tuple(sorted(item["dimensions"].items())), point["timestamp"]): float(point["value"])
        for item in left_series for point in item["points"]
    }
    right = {
        (tuple(sorted(item["dimensions"].items())), point["timestamp"]): float(point["value"])
        for item in right_series for point in item["points"]
    }
    output: dict[tuple, list[dict[str, Any]]] = {}
    for (dimensions, timestamp), left_value in left.items():
        if (dimensions, timestamp) not in right:
            continue
        right_value = right[(dimensions, timestamp)]
        if operator == "add":
            value = left_value + right_value
        elif operator == "subtract":
            value = left_value - right_value
        elif operator == "multiply":
            value = left_value * right_value
        else:
            if right_value == 0:
                continue
            value = left_value / right_value
        output.setdefault(dimensions, []).append({"timestamp": timestamp, "value": value})
    return [
        {"dimensions": dict(dimensions), "points": sorted(points, key=lambda point: point["timestamp"])}
        for dimensions, points in output.items()
    ]


def _apply_metric_operation(
    series: list[dict[str, Any]], operation: MetricOperation, current_step: int, origin: int, end: int,
) -> tuple[list[dict[str, Any]], int]:
    validate_operation(operation.operation, "gauge", initial=False)
    whole_range = operation.interval_seconds is None
    step = end - origin if whole_range else operation.interval_seconds
    if not whole_range and (step < current_step or step % current_step):
        raise ValueError(f"Interval must be a multiple of the current resolution ({current_step}s)")
    grouped: dict[tuple, list[tuple[int, float]]] = {}
    for item in series:
        dimensions = item["dimensions"]
        group_values = tuple((name, dimensions.get(name)) for name in operation.group_by)
        for point in item["points"]:
            slot = origin if whole_range else int(point["timestamp"] // step) * step
            grouped.setdefault((slot, group_values), []).append((int(point["timestamp"]), float(point["value"])))

    output: dict[tuple, list[dict[str, Any]]] = {}
    for (slot, group_values), values in grouped.items():
        numbers = [value for _, value in values]
        op = operation.operation
        if op == "count":
            value = float(len(numbers))
        elif op == "sum":
            value = sum(numbers)
        elif op == "mean":
            value = sum(numbers) / len(numbers)
        elif op == "min":
            value = min(numbers)
        elif op == "max":
            value = max(numbers)
        else:
            p = int(op[1:]) / 100
            ordered = sorted(numbers)
            index = (len(ordered) - 1) * p
            low = math.floor(index)
            high = math.ceil(index)
            value = ordered[low] + (ordered[high] - ordered[low]) * (index - low)
        labels = dict(group_values)
        output.setdefault(tuple(sorted(labels.items())), []).append({"timestamp": slot, "value": value})
    return [
        {"dimensions": dict(labels), "points": sorted(points, key=lambda point: point["timestamp"])}
        for labels, points in output.items()
    ], step
