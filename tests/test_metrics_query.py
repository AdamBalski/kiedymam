import tempfile
import time
import unittest
from pathlib import Path

from app.metrics.query import ArithmeticOperation, MetricOperation, Predicate, QuerySpec, execute_query
from app.metrics.store import MetricsStore


class MetricsQueryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = MetricsStore(str(Path(self.temp.name) / "metrics.sqlite3"), "test-version")

    def tearDown(self):
        self.temp.cleanup()

    def test_query_adds_another_query_then_turns_the_result_into_a_signal(self):
        now = MetricsStore._bucket_start()
        self.store.add_counter("test.left", 4, {"route": "/home"})
        self.store.add_counter("test.right", 2, {"route": "/home"})
        self.store.flush(now=now)

        operation = MetricOperation("sum", group_by=("route",), interval_seconds=60)
        right_query = QuerySpec(
            "test.right", now - 60, now + 60, {}, (operation,),
        )
        query = QuerySpec(
            "test.left", now - 60, now + 60, {},
            (operation, ArithmeticOperation("add", right_query), Predicate(">=", 6)),
        )

        result = execute_query(self.store, query)

        self.assertEqual(result["step_seconds"], 60)
        self.assertEqual(result["series"][0]["dimensions"], {"route": "/home"})
        self.assertEqual(result["series"][0]["points"][0]["value"], 1.0)

    def test_query_rejects_arithmetic_with_different_interval_resolutions(self):
        now = int(time.time())
        self.store.add_counter("test.left", 1)
        self.store.add_counter("test.right", 1)
        self.store.flush(now=now)
        left_operation = MetricOperation("sum", interval_seconds=60)
        right_operation = MetricOperation("sum", interval_seconds=120)
        right_query = QuerySpec(
            "test.right", now - 60, now + 60, {}, (right_operation,),
        )
        query = QuerySpec(
            "test.left", now - 60, now + 60, {},
            (left_operation, ArithmeticOperation("divide", right_query)),
        )

        with self.assertRaisesRegex(ValueError, "same interval resolution"):
            execute_query(self.store, query)

    def test_query_supports_all_arithmetic_operations(self):
        now = MetricsStore._bucket_start()
        operations = {"add": 12, "subtract": 8, "multiply": 20, "divide": 5}
        for name in operations:
            self.store.add_counter(f"test.left.{name}", 10)
            self.store.add_counter(f"test.right.{name}", 2)
        self.store.flush(now=now)

        for operator, expected in operations.items():
            with self.subTest(operator=operator):
                metric_operation = MetricOperation("sum", interval_seconds=60)
                operand = QuerySpec(
                    f"test.right.{operator}", now - 60, now + 60, {}, (metric_operation,),
                )
                query = QuerySpec(
                    f"test.left.{operator}", now - 60, now + 60, {},
                    (metric_operation, ArithmeticOperation(operator, operand), Predicate("=", expected)),
                )
                result = execute_query(self.store, query)
                self.assertEqual(result["series"][0]["points"][0]["value"], 1.0)


if __name__ == "__main__":
    unittest.main()
