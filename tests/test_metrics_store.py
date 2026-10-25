import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.metrics.store import MetricsStore


class MetricsStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = MetricsStore(str(Path(self.temp.name) / "metrics.sqlite3"), "test-version")

    def tearDown(self):
        self.temp.cleanup()

    def test_store_records_arbitrary_metric_names_without_owning_metadata(self):
        now = int(time.time())
        self.store.add_counter("application.custom.count", 3, {"custom.dimension": "alpha"})
        self.store.flush(now=now)

        dimensions = self.store.dimensions("application.custom.count")
        self.assertEqual(dimensions[0]["name"], "custom.dimension")
        result = self.store.query("application.custom.count", now - 5, now + 5)
        self.assertEqual(result["series"][0]["points"][0]["value"], 3)
        with self.store.database.read() as db:
            table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='metrics_catalog'").fetchone()
        self.assertIsNone(table)

    def test_gauges_are_sampled_even_when_the_value_does_not_change(self):
        now = int(time.time())
        self.store.set_gauge("application.load", 4.5)
        self.store.flush(now=now)
        self.store.set_gauge("application.load", 4.5)
        self.store.flush(now=now + 5)

        result = self.store.query("application.load", now - 1, now + 10, interval_seconds=5)
        self.assertEqual(len(result["series"][0]["points"]), 2)
        self.assertEqual([point["value"] for point in result["series"][0]["points"]], [4.5, 4.5])

    def test_counter_series_writes_zero_for_a_registered_empty_interval(self):
        now = MetricsStore._bucket_start()
        self.store.add_counter("application.requests", 2)
        self.store.flush(now=now)
        self.store.flush(now=now + 5)

        result = self.store.query("application.requests", now, now + 10, interval_seconds=5)
        self.assertEqual([point["value"] for point in result["series"][0]["points"]], [2, 0])

    def test_histograms_store_only_observed_buckets(self):
        now = int(time.time())
        self.store.observe("application.duration", 0.03)
        self.store.observe("application.duration", 7.0)
        self.store.flush(now=now)

        with self.store.database.read() as db:
            rows = db.execute(
                "SELECT point_kind,bucket_upper,sample_count FROM metrics_points WHERE metric_name=?",
                ("application.duration",),
            ).fetchall()
        summaries = [row for row in rows if row["point_kind"] == "hist_summary"]
        buckets = [row for row in rows if row["point_kind"] == "hist_bucket"]
        self.assertEqual(len(summaries), 1)
        self.assertEqual(len(buckets), 2)
        self.assertTrue(all(row["sample_count"] == 1 for row in buckets))
        self.assertEqual(len({row["bucket_upper"] for row in buckets}), 2)

    def test_query_operation_groups_by_dimensions_and_uses_requested_interval(self):
        now = MetricsStore._bucket_start()
        self.store.add_counter("application.requests", 2, {"status": 200})
        self.store.add_counter("application.requests", 3, {"status": 200})
        self.store.add_counter("application.requests", 5, {"status": 500})
        self.store.flush(now=now)

        result = self.store.query(
            "application.requests", now, now + 60,
            operation="sum", group_by=["status"], interval_seconds=60,
        )

        self.assertEqual(result["step_seconds"], 60)
        self.assertEqual(result["operation"], "sum")
        self.assertEqual(
            {series["dimensions"]["status"]: series["points"][0]["value"] for series in result["series"]},
            {200: 5, 500: 5},
        )

    def test_missing_interval_aggregates_the_entire_requested_range(self):
        now = int(time.time() // 60) * 60
        with patch("app.metrics.store.time.time", return_value=now - 5):
            self.store.add_counter("application.requests", 2)
            self.store.flush(now=now - 5)
        with patch("app.metrics.store.time.time", return_value=now):
            self.store.add_counter("application.requests", 3)
            self.store.flush(now=now)

        result = self.store.query(
            "application.requests", now - 10, now + 50,
            operation="sum",
        )

        points = result["series"][0]["points"]
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0], {"timestamp": now - 10, "value": 5})

    def test_histogram_percentile_is_calculated_after_merging_the_interval(self):
        now = MetricsStore._bucket_start()
        for _ in range(5):
            self.store.observe("application.duration", 0.03)
        self.store.flush(now=now)
        self.store.observe("application.duration", 7.0)
        self.store.flush(now=now + 5)

        result = self.store.query(
            "application.duration", now, now + 60,
            operation="p50", interval_seconds=60,
        )

        points = result["series"][0]["points"]
        self.assertEqual(len(points), 1)
        self.assertLess(points[0]["value"], 0.05)

    def test_query_rejects_interval_finer_than_retention_resolution(self):
        now = int(time.time())
        self.store.add_counter("application.requests", 1)
        self.store.flush(now=now)
        with self.assertRaisesRegex(ValueError, "minimum resolution"):
            self.store.query(
                "application.requests", now - 8 * 86400, now,
                operation="sum", interval_seconds=5,
            )

    def test_percentile_operations_are_encoded_in_the_operation_name(self):
        now = int(time.time())
        self.store.observe("application.duration", 0.03)
        self.store.add_counter("application.requests", 1)
        self.store.flush(now=now)
        with self.assertRaisesRegex(ValueError, "Unsupported operation"):
            self.store.query("application.duration", now - 5, now + 5, operation="p101")
        with self.assertRaisesRegex(ValueError, "Unsupported operation"):
            self.store.query("application.requests", now - 5, now + 5, operation="p95")


if __name__ == "__main__":
    unittest.main()
