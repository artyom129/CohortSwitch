from prometheus_client import Counter, Histogram

HTTP_REQUESTS = Counter(
    "cohortswitch_http_requests_total",
    "HTTP requests processed by route.",
    ("method", "route", "status"),
)
HTTP_DURATION = Histogram(
    "cohortswitch_http_request_duration_seconds",
    "HTTP request duration by route.",
    ("method", "route"),
)
EVALUATIONS = Counter(
    "cohortswitch_evaluations_total",
    "Feature flag evaluation decisions.",
    ("environment", "reason", "variation"),
)
CACHE_OPERATIONS = Counter(
    "cohortswitch_cache_operations_total",
    "Configuration cache outcomes.",
    ("operation", "outcome"),
)
