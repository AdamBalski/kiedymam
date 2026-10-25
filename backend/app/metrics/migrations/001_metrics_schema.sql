CREATE TABLE metrics_catalog (
    metric_name TEXT PRIMARY KEY,
    metric_type TEXT NOT NULL CHECK(metric_type IN ('counter','gauge','histogram')),
    unit TEXT NOT NULL
);

CREATE TABLE metrics_points (
    metric_name TEXT NOT NULL,
    attributes_json TEXT NOT NULL,
    bucket_start INTEGER NOT NULL,
    granularity_seconds INTEGER NOT NULL,
    point_kind TEXT NOT NULL,
    bucket_upper REAL NOT NULL DEFAULT -1,
    sample_count INTEGER NOT NULL DEFAULT 0,
    sum_value REAL NOT NULL DEFAULT 0,
    min_value REAL,
    max_value REAL,
    PRIMARY KEY(metric_name, attributes_json, bucket_start, granularity_seconds, point_kind, bucket_upper)
);
CREATE INDEX metrics_points_window_idx
    ON metrics_points(metric_name, granularity_seconds, bucket_start);
