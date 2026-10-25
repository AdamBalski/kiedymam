from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
import logging
import math
from pathlib import Path
import re
import threading
import time
from typing import Any

from app.db.database import Database

LOG = logging.getLogger(__name__)
RAW_STEP = 5
HISTOGRAM_BOUNDS = (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0)
HIST_OVERFLOW = 1e308
_POINT_KIND_TO_METRIC_TYPE = {
    "counter": "counter",
    "gauge": "gauge",
    "hist_summary": "histogram",
    "hist_bucket": "histogram",
}


def _metric_type_for_point_kind(point_kind: str) -> str:
    try:
        return _POINT_KIND_TO_METRIC_TYPE[point_kind]
    except KeyError as exc:
        raise ValueError(f"Unsupported metric point kind: {point_kind}") from exc


def _attrs_json(attributes: dict[str, Any] | None) -> str:
    normalized = {}
    for key, value in (attributes or {}).items():
        if not isinstance(key, str) or not key:
            raise ValueError("Metric attribute names must be non-empty strings")
        if isinstance(value, (str, int, float, bool)) and not (isinstance(value, float) and not math.isfinite(value)):
            normalized[key] = value
        else:
            raise ValueError(f"Metric attribute {key} must be a scalar")
    return json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class MetricsStore:
    def __init__(self, path: str, service_version: str = "unknown"):
        migrations = Path(__file__).with_name("migrations")
        self.database = Database(path, migrations, "metrics_schema_migrations")
        self.database.initialize()
        self.service_name = "kiedymam"
        self.service_version = service_version or "unknown"
        self._lock = threading.Lock()
        self._counters: dict[tuple, float] = defaultdict(float)
        self._counter_series: set[tuple] = set()
        self._gauges: dict[tuple, float] = {}
        self._histograms: dict[tuple, dict[str, Any]] = {}
        self._last_rollup_minute = -1

    @staticmethod
    def _bucket_start(timestamp: float | None = None) -> int:
        current = time.time() if timestamp is None else timestamp
        return int(current // RAW_STEP) * RAW_STEP

    @staticmethod
    def _validate_name(name: str) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Metric name must be a non-empty string")

    def add_counter(self, name: str, value: float = 1, attributes: dict[str, Any] | None = None) -> None:
        try:
            self._validate_name(name)
            if not math.isfinite(value) or value < 0:
                raise ValueError("Counter increments must be finite and non-negative")
            attrs = _attrs_json(attributes)
        except Exception:
            LOG.exception("Could not record counter metric name=%s", name)
            return
        key = (name, attrs)
        bucket = self._bucket_start()
        with self._lock:
            self._counter_series.add(key)
            self._counters[(name, attrs, bucket)] += value

    def set_gauge(self, name: str, value: float, attributes: dict[str, Any] | None = None) -> None:
        try:
            self._validate_name(name)
            if not math.isfinite(value):
                return
            attrs = _attrs_json(attributes)
        except Exception:
            LOG.exception("Could not record gauge metric name=%s", name)
            return
        with self._lock:
            self._gauges[(name, attrs)] = value

    def observe(self, name: str, value: float, attributes: dict[str, Any] | None = None) -> None:
        try:
            self._validate_name(name)
            if not math.isfinite(value) or value < 0:
                return
            attrs = _attrs_json(attributes)
        except Exception:
            LOG.exception("Could not record histogram metric name=%s", name)
            return
        bucket_start = self._bucket_start()
        bound = next((item for item in HISTOGRAM_BOUNDS if value <= item), HIST_OVERFLOW)
        key = (name, attrs, bucket_start)
        with self._lock:
            record = self._histograms.setdefault(key, {"count": 0, "sum": 0.0, "min": value, "max": value, "buckets": {}})
            record["count"] += 1
            record["sum"] += value
            record["min"] = min(record["min"], value)
            record["max"] = max(record["max"], value)
            record["buckets"][bound] = record["buckets"].get(bound, 0) + 1

    def flush(self, now: float | None = None) -> None:
        timestamp = time.time() if now is None else now
        current_bucket = self._bucket_start(timestamp)
        with self._lock:
            counters = dict(self._counters)
            counter_series = set(self._counter_series)
            gauges = dict(self._gauges)
            histograms = dict(self._histograms)
            self._counters.clear()
            self._histograms.clear()

        points: list[tuple] = []
        for name, attrs in counter_series:
            value = counters.get((name, attrs, current_bucket), 0.0)
            points.append((name, attrs, current_bucket, RAW_STEP, "counter", -1.0, 1, value, value, value))
        for (name, attrs), value in gauges.items():
            points.append((name, attrs, current_bucket, RAW_STEP, "gauge", -1.0, 1, value, value, value))
        for (name, attrs, bucket_start), record in histograms.items():
            points.append((name, attrs, bucket_start, RAW_STEP, "hist_summary", -1.0,
                           record["count"], record["sum"], record["min"], record["max"]))
            for bound, count in record["buckets"].items():
                points.append((name, attrs, bucket_start, RAW_STEP, "hist_bucket", float(bound),
                               count, 0.0, None, None))
        for (name, attrs, bucket_start), value in counters.items():
            if bucket_start != current_bucket and (name, attrs, bucket_start) in counters:
                points.append((name, attrs, bucket_start, RAW_STEP, "counter", -1.0, 1, value, value, value))

        try:
            self._write_points(points)
            minute = int(timestamp // 60)
            if minute != self._last_rollup_minute:
                self._rollup(timestamp)
                self._last_rollup_minute = minute
        except Exception:
            LOG.exception("Metrics flush failed; measurements will be restored for retry")
            with self._lock:
                for key, value in counters.items():
                    self._counters[key] += value
                self._histograms.update(histograms)

    def _write_points(self, points: list[tuple]) -> None:
        if not points:
            return
        metric_types: dict[str, str] = {}
        for point in points:
            name, point_kind = point[0], point[4]
            metric_type = _metric_type_for_point_kind(point_kind)
            existing_type = metric_types.setdefault(name, metric_type)
            if existing_type != metric_type:
                raise ValueError(f"Metric {name!r} was recorded with conflicting types")
        with self.database.transaction(immediate=True) as db:
            for name, metric_type in metric_types.items():
                existing = db.execute(
                    "SELECT metric_type FROM metrics_index WHERE metric_name=?", (name,),
                ).fetchone()
                if existing is not None and existing["metric_type"] != metric_type:
                    raise ValueError(f"Metric {name!r} was recorded with conflicting types")
                db.execute(
                    "INSERT INTO metrics_index(metric_name,metric_type) VALUES(?,?) "
                    "ON CONFLICT(metric_name) DO NOTHING",
                    (name, metric_type),
                )
            db.executemany(
                """INSERT INTO metrics_points(
                       metric_name,attributes_json,bucket_start,granularity_seconds,point_kind,
                       bucket_upper,sample_count,sum_value,min_value,max_value
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(metric_name,attributes_json,bucket_start,granularity_seconds,point_kind,bucket_upper)
                   DO UPDATE SET sample_count=metrics_points.sample_count+excluded.sample_count,
                     sum_value=metrics_points.sum_value+excluded.sum_value,
                     min_value=CASE WHEN metrics_points.min_value IS NULL THEN excluded.min_value
                                    WHEN excluded.min_value IS NULL THEN metrics_points.min_value
                                    ELSE MIN(metrics_points.min_value,excluded.min_value) END,
                     max_value=CASE WHEN metrics_points.max_value IS NULL THEN excluded.max_value
                                    WHEN excluded.max_value IS NULL THEN metrics_points.max_value
                                    ELSE MAX(metrics_points.max_value,excluded.max_value) END""",
                points,
            )

    def _rollup(self, now: float) -> None:
        thresholds = ((RAW_STEP, 60, 3600), (60, 3600, 7 * 86400),
                      (3600, 86400, 30 * 86400))
        with self.database.transaction(immediate=True) as db:
            for source_step, target_step, age in thresholds:
                cutoff = int((now - age) // target_step) * target_step
                rows = db.execute(
                    "SELECT * FROM metrics_points WHERE granularity_seconds=? AND bucket_start+granularity_seconds<=?",
                    (source_step, cutoff),
                ).fetchall()
                grouped: dict[tuple, list] = {}
                for row in rows:
                    target_start = int(row["bucket_start"] // target_step) * target_step
                    key = (row["metric_name"], row["attributes_json"], target_start,
                           target_step, row["point_kind"], row["bucket_upper"])
                    grouped.setdefault(key, []).append(row)
                for key, source_rows in grouped.items():
                    count = sum(row["sample_count"] for row in source_rows)
                    total = sum(row["sum_value"] for row in source_rows)
                    minimum = min((row["min_value"] for row in source_rows if row["min_value"] is not None), default=None)
                    maximum = max((row["max_value"] for row in source_rows if row["max_value"] is not None), default=None)
                    db.execute(
                        """INSERT INTO metrics_points VALUES(?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(metric_name,attributes_json,bucket_start,granularity_seconds,point_kind,bucket_upper)
                           DO UPDATE SET sample_count=excluded.sample_count,sum_value=excluded.sum_value,
                             min_value=excluded.min_value,max_value=excluded.max_value""",
                        (*key, count, total, minimum, maximum),
                    )
                if rows:
                    db.executemany("DELETE FROM metrics_points WHERE metric_name=? AND attributes_json=? AND bucket_start=? AND granularity_seconds=? AND point_kind=? AND bucket_upper=?",
                                   [(row["metric_name"], row["attributes_json"], row["bucket_start"], row["granularity_seconds"], row["point_kind"], row["bucket_upper"]) for row in rows])
            daily_cutoff = int((now - 13 * 30.4375 * 86400) // 86400) * 86400
            db.execute("DELETE FROM metrics_points WHERE granularity_seconds=86400 AND bucket_start+granularity_seconds<=?", (daily_cutoff,))
            db.execute(
                "DELETE FROM metrics_index WHERE NOT EXISTS "
                "(SELECT 1 FROM metrics_points WHERE metrics_points.metric_name=metrics_index.metric_name)",
            )

    def dimensions(self, name: str) -> list[dict[str, Any]]:
        available: dict[str, dict[str, set[Any]]] = defaultdict(lambda: defaultdict(set))
        with self.database.read() as db:
            rows = db.execute(
                "SELECT DISTINCT attributes_json FROM metrics_points WHERE metric_name=?", (name,),
            ).fetchall()
        for row in rows:
            try:
                attrs = json.loads(row["attributes_json"])
            except json.JSONDecodeError:
                continue
            for key, value in attrs.items():
                available[name][key].add(value)
        return [{"name": key, "values": sorted(values, key=str)[:500]}
                for key, values in sorted(available[name].items())]

    def metric_type(self, name: str) -> str:
        """Return the discovered type for a stored metric name."""
        with self.database.read() as db:
            row = db.execute(
                "SELECT metric_type FROM metrics_index WHERE metric_name=?",
                (name,),
            ).fetchone()
        if row is None:
            raise ValueError(f"Unknown metric {name!r}")
        return row["metric_type"]

    def catalog(self) -> list[dict[str, Any]]:
        """List metrics discovered in samples, including their type and dimensions."""
        with self.database.read() as db:
            rows = db.execute(
                "SELECT metric_name, metric_type FROM metrics_index ORDER BY metric_name",
            ).fetchall()
        result = []
        for row in rows:
            name = row["metric_name"]
            kind = row["metric_type"]
            result.append({
                "name": name,
                "type": kind,
                "dimensions": self.dimensions(name),
                "histogram_bounds": list(HISTOGRAM_BOUNDS) if kind == "histogram" else [],
            })
        return result

    def query(
        self, name: str, start: int, end: int, *, filters: dict[str, Any] | None = None,
        group_by: list[str] | None = None, operation: str = "sum", interval_seconds: int | None = None,
    ) -> dict[str, Any]:
        kind = self.metric_type(name)
        if kind not in {"counter", "gauge", "histogram"}:
            raise ValueError(f"Unsupported metric type: {kind}")
        if end <= start or end - start > 13 * 31 * 86400:
            raise ValueError("Choose a valid time range no longer than 13 months")
        filters = filters or {}
        group_by = group_by or []
        if any(not isinstance(key, str) or not key for key in filters) or any(not isinstance(key, str) or not key for key in group_by):
            raise ValueError("Metric dimension names must be non-empty strings")
        if kind == "histogram":
            allowed_operations = {"count", "sum", "mean", "min", "max"}
        else:
            allowed_operations = {"count", "sum", "mean", "min", "max"}
        percentile_match = re.fullmatch(r"p(100|[1-9]?\d)", operation)
        if operation not in allowed_operations and not (kind == "histogram" and percentile_match):
            raise ValueError(f"Unsupported operation for {kind} metrics")
        base_step = 5 if end - start <= 3600 else 60 if end - start <= 7 * 86400 else 3600 if end - start <= 30 * 86400 else 86400
        whole_range = interval_seconds is None
        step = end - start if whole_range else interval_seconds
        if not whole_range and (step < base_step or step % base_step):
            raise ValueError(f"Interval must be a multiple of the minimum resolution ({base_step}s)")
        with self.database.read() as db:
            rows = db.execute(
                "SELECT * FROM metrics_points WHERE metric_name=? AND bucket_start<? AND bucket_start+granularity_seconds>? ORDER BY bucket_start",
                (name, end, start),
            ).fetchall()
        parsed_rows = []
        for row in rows:
            attrs = json.loads(row["attributes_json"])
            if any(str(attrs.get(key)) != str(value) for key, value in filters.items()):
                continue
            parsed_rows.append((row, attrs))

        grouped_points: dict[tuple, dict[str, Any]] = {}
        if kind == "histogram":
            distributions: dict[tuple, dict[str, Any]] = {}
            for row, attrs in parsed_rows:
                group_values = tuple((key, attrs.get(key)) for key in group_by)
                slot = start if whole_range else int(row["bucket_start"] // step) * step
                key = (slot, group_values)
                target = distributions.setdefault(key, {"summary": None, "buckets": defaultdict(int)})
                if row["point_kind"] == "hist_summary":
                    summary = target["summary"]
                    if summary is None:
                        target["summary"] = {
                            "sample_count": int(row["sample_count"]), "sum_value": float(row["sum_value"]),
                            "min_value": row["min_value"], "max_value": row["max_value"],
                        }
                    else:
                        summary["sample_count"] += int(row["sample_count"])
                        summary["sum_value"] += float(row["sum_value"])
                        summary["min_value"] = min(summary["min_value"], row["min_value"])
                        summary["max_value"] = max(summary["max_value"], row["max_value"])
                elif row["point_kind"] == "hist_bucket":
                    target["buckets"][float(row["bucket_upper"])] += int(row["sample_count"])
            for (slot, group_values), distribution in distributions.items():
                summary = distribution["summary"]
                if not summary or not distribution["buckets"]:
                    continue
                buckets = [{"bucket_upper": bound, "sample_count": count}
                           for bound, count in distribution["buckets"].items()]
                value = self._reduce_histogram(buckets, summary, operation)
                grouped_points[(slot, group_values)] = {"value": value}
        else:
            for row, attrs in parsed_rows:
                if row["point_kind"] not in ("counter", "gauge"):
                    continue
                group_values = tuple((key, attrs.get(key)) for key in group_by)
                slot = start if whole_range else int(row["bucket_start"] // step) * step
                key = (slot, group_values)
                acc = grouped_points.setdefault(key, {"values": [], "sum": 0.0, "count": 0, "min": None, "max": None})
                value = row["sum_value"] / row["sample_count"] if row["sample_count"] else 0.0
                if kind == "counter" and operation == "sum":
                    value = row["sum_value"]
                acc["values"].append(value)
                acc["sum"] += row["sum_value"] if kind == "counter" else value * row["sample_count"]
                acc["count"] += row["sample_count"]
                minimum = row["min_value"] if row["min_value"] is not None else value
                maximum = row["max_value"] if row["max_value"] is not None else value
                acc["min"] = minimum if acc["min"] is None else min(acc["min"], minimum)
                acc["max"] = maximum if acc["max"] is None else max(acc["max"], maximum)

        series: dict[tuple, list] = defaultdict(list)
        for (slot, group_values), acc in grouped_points.items():
            if kind == "histogram":
                value = acc["value"]
            elif operation == "count":
                value = acc["count"]
            elif operation == "sum":
                value = acc["sum"]
            elif operation == "mean":
                value = acc["sum"] / acc["count"] if acc["count"] else 0
            elif operation == "min":
                value = acc["min"]
            else:
                value = acc["max"]
            labels = dict(group_values)
            series[tuple(sorted(labels.items()))].append({"timestamp": slot, "value": value})
        return {
            "metric": name, "type": kind,
            "step_seconds": step, "operation": operation,
            "service": {"name": self.service_name, "version": self.service_version},
            "series": [{"dimensions": dict(labels), "points": sorted(points, key=lambda point: point["timestamp"])}
                       for labels, points in series.items()],
        }

    @staticmethod
    def _reduce_histogram(buckets: list, summary, operation: str) -> float:
        count = int(summary["sample_count"])
        total = float(summary["sum_value"])
        if operation == "count":
            return float(count)
        if operation == "sum":
            return total
        if operation == "mean":
            return total / count if count else 0.0
        if operation == "min":
            return float(summary["min_value"])
        if operation == "max":
            return float(summary["max_value"])
        sorted_buckets = sorted(buckets, key=lambda row: row["bucket_upper"])
        percentile = int(operation[1:])
        target = percentile / 100 * max(0, count - 1)
        cumulative = 0
        previous_bound = 0.0
        for row in sorted_buckets:
            upper = float(row["bucket_upper"])
            bucket_count = int(row["sample_count"])
            if cumulative + bucket_count > target:
                if upper >= HIST_OVERFLOW:
                    return max(previous_bound, float(summary["max_value"]))
                return (previous_bound + upper) / 2
            cumulative += bucket_count
            previous_bound = upper
        return float(summary["max_value"])

