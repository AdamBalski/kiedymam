DELETE FROM metrics_points
WHERE metric_name IN (
    'http.server.request.count',
    'google.calendar.api.call.count',
    'google.oauth.refresh.count',
    'push.delivery.count',
    'sqlite.operation.count',
    'xlsx.download.count',
    'xlsx.parser.result'
);

DELETE FROM metrics_index
WHERE metric_name IN (
    'http.server.request.count',
    'google.calendar.api.call.count',
    'google.oauth.refresh.count',
    'push.delivery.count',
    'sqlite.operation.count',
    'xlsx.download.count',
    'xlsx.parser.result'
);
