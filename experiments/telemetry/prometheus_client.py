"""Prometheus range-query client with typed response parsing."""

import logging
import math
import time
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class MetricSample(BaseModel):
    """A single timestamped metric sample."""

    timestamp: datetime
    value: float | None = None


class MetricSeries(BaseModel):
    """A metric time series with associated labels and values."""

    labels: dict[str, str] = Field(default_factory=dict)
    samples: list[MetricSample] = Field(default_factory=list)


class RangeQueryResult(BaseModel):
    """Result of a Prometheus range query."""

    field_name: str
    query: str
    result_type: str
    series: list[MetricSeries] = Field(default_factory=list)


class PrometheusClient:
    """Client for executing range queries against Prometheus."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:19090",
        timeout_seconds: float = 10.0,
        max_retries: int = 3,
        allow_non_local: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

        # Enforce localhost unless explicitly permitted
        parsed = urlparse(self.base_url)
        hostname = (parsed.hostname or "").lower()
        if not allow_non_local and hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError(
                f"PrometheusClient host must be localhost ({hostname} provided). "
                "Set allow_non_local=True to override for testing."
            )

    def query_range(
        self,
        field_name: str,
        promql: str,
        start_time: datetime | float,
        end_time: datetime | float,
        step: str = "15s",
    ) -> RangeQueryResult:
        """Execute a range query and return structured, parsed series."""
        start_ts = start_time.timestamp() if isinstance(start_time, datetime) else float(start_time)
        end_ts = end_time.timestamp() if isinstance(end_time, datetime) else float(end_time)

        if start_ts > end_ts:
            raise ValueError(f"start_time ({start_ts}) cannot be after end_time ({end_ts})")

        url = f"{self.base_url}/api/v1/query_range"
        params = {
            "query": promql,
            "start": f"{start_ts:.3f}",
            "end": f"{end_ts:.3f}",
            "step": step,
        }

        last_err: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    resp = client.get(url, params=params)

                if resp.status_code != 200:
                    raise RuntimeError(
                        f"Prometheus HTTP {resp.status_code} error: {resp.text[:500]}"
                    )

                payload = resp.json()
                if not isinstance(payload, dict):
                    raise ValueError(f"Malformed Prometheus JSON response: {payload}")

                if payload.get("status") != "success":
                    error_type = payload.get("errorType", "UnknownError")
                    error_msg = payload.get("error", "No error message provided")
                    raise RuntimeError(f"Prometheus API error [{error_type}]: {error_msg}")

                data = payload.get("data", {})
                result_type = data.get("resultType", "")
                raw_results = data.get("result", [])

                series_list = []
                for item in raw_results:
                    labels = item.get("metric", {})
                    raw_values = item.get("values", [])
                    samples = []
                    for ts_raw, val_raw in raw_values:
                        # Convert epoch timestamp to timezone-aware UTC datetime
                        sample_dt = datetime.fromtimestamp(float(ts_raw), tz=UTC)
                        parsed_val = self._parse_sample_value(val_raw)
                        samples.append(MetricSample(timestamp=sample_dt, value=parsed_val))
                    series_list.append(MetricSeries(labels=labels, samples=samples))

                return RangeQueryResult(
                    field_name=field_name,
                    query=promql,
                    result_type=result_type,
                    series=series_list,
                )

            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                last_err = exc
                logger.warning(
                    f"Prometheus query attempt {attempt}/{self.max_retries} failed: {exc}"
                )
                if attempt < self.max_retries:
                    time.sleep(0.5 * attempt)
            except Exception:
                raise

        raise RuntimeError(
            f"Failed to query Prometheus after {self.max_retries} attempts: {last_err}"
        )

    @staticmethod
    def _parse_sample_value(raw: str | float | None) -> float | None:
        """Safely parse Prometheus sample string values, handling NaN/Inf."""
        if raw is None:
            return None
        val_str = str(raw).strip()
        if val_str == "NaN":
            return None
        if val_str in ("+Inf", "Inf"):
            return None
        if val_str == "-Inf":
            return None
        try:
            val = float(val_str)
            if math.isnan(val) or math.isinf(val):
                return None
            return val
        except (ValueError, TypeError):
            return None
