CREATE TABLE metrics_index (
    metric_name TEXT PRIMARY KEY,
    metric_type TEXT NOT NULL CHECK(metric_type IN ('counter','gauge','histogram'))
);

INSERT INTO metrics_index(metric_name, metric_type)
SELECT metric_name,
       CASE
           WHEN COUNT(DISTINCT CASE
               WHEN point_kind IN ('counter', 'gauge') THEN point_kind
               WHEN point_kind IN ('hist_summary', 'hist_bucket') THEN 'histogram'
               ELSE 'invalid'
           END) = 1
           THEN MIN(CASE
               WHEN point_kind IN ('counter', 'gauge') THEN point_kind
               WHEN point_kind IN ('hist_summary', 'hist_bucket') THEN 'histogram'
               ELSE 'invalid'
           END)
           ELSE 'invalid'
       END
FROM metrics_points
GROUP BY metric_name;
