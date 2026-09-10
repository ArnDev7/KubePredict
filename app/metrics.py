from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, Info


class AppMetrics:
    def __init__(self, registry: CollectorRegistry):
        self.registry = registry

        self.http_requests_total = Counter(
            "apa_http_requests_total",
            "Total HTTP requests",
            ["method", "route", "status_code"],
            registry=self.registry,
        )

        self.http_request_duration_seconds = Histogram(
            "apa_http_request_duration_seconds",
            "HTTP request duration in seconds",
            ["method", "route"],
            buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
            registry=self.registry,
        )

        self.http_requests_in_progress = Gauge(
            "apa_http_requests_in_progress",
            "In progress HTTP requests",
            ["method", "route"],
            registry=self.registry,
        )

        self.work_requests_total = Counter(
            "apa_work_requests_total",
            "Total work requests by outcome",
            ["outcome"],
            registry=self.registry,
        )

        self.cpu_iterations_total = Counter(
            "apa_cpu_iterations_total", "Total CPU iterations completed", registry=self.registry
        )

        self.artificial_delay_seconds_total = Counter(
            "apa_artificial_delay_seconds_total",
            "Total artificial delay applied in seconds",
            registry=self.registry,
        )

        self.work_duration_seconds = Histogram(
            "apa_work_duration_seconds",
            "Work duration in seconds",
            buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 2.5, 5.0, 10.0],
            registry=self.registry,
        )

        self.application_info = Info(
            "apa_application", "Application information", registry=self.registry
        )
