DELETE FROM metrics_points
WHERE point_kind = 'counter' AND sum_value = 0;

DELETE FROM metrics_index
WHERE NOT EXISTS (
    SELECT 1
    FROM metrics_points
    WHERE metrics_points.metric_name = metrics_index.metric_name
);
