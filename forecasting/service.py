"""Pure in-process forecast service wrapper for KubePredict."""

import copy
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from forecasting.base import BaseForecaster, ForecastInput, ForecastOutput
from forecasting.ewma import EWMAForecaster
from forecasting.linear_trend import LinearTrendForecaster
from forecasting.moving_average import MovingAverageForecaster
from forecasting.persistence import PersistenceForecaster


def instantiate_forecaster(model_family: str, parameters: dict[str, Any]) -> BaseForecaster:
    """Instantiate a BaseForecaster instance from model family and parameters."""
    family = model_family.lower().strip()
    if family == "persistence":
        return PersistenceForecaster()
    elif family == "moving_average":
        return MovingAverageForecaster(window=int(parameters.get("window", 3)))
    elif family == "ewma":
        return EWMAForecaster(alpha=float(parameters.get("alpha", 0.8)))
    elif family == "linear_trend":
        return LinearTrendForecaster(window=int(parameters.get("window", 3)))
    else:
        raise ValueError(f"Unsupported model family: '{model_family}'")


class ForecastingService:
    """Pure in-process forecast service wrapper.

    Enforces runtime forecast contracts, validates observation histories,
    produces deterministic ForecastOutput point forecasts, and provides
    automatic fallback to Persistence when history length is insufficient.
    No network calls, file writes, replica logic, or Kubernetes dependencies exist.
    """

    def __init__(
        self,
        config: dict[str, Any],
        model_override: BaseForecaster | None = None,
    ) -> None:
        """Initialize ForecastingService with frozen configuration."""
        self.config = copy.deepcopy(config)
        self.schema_version = str(self.config.get("schema_version", "1.0.0"))
        self.status = str(self.config.get("status", "offline_selected"))
        if self.status == "production_ready":
            raise ValueError(
                "Status 'production_ready' is forbidden in offline evaluation phases; "
                "expected 'offline_selected'"
            )

        self.model_family = str(self.config.get("model_family", "persistence"))
        self.model_name = str(self.config.get("model_name", self.model_family))
        self.model_version = str(self.config.get("model_version", "1.0.0"))
        self.parameters: dict[str, Any] = self.config.get("parameters", {})
        self.sampling_interval_seconds = float(self.config.get("sampling_interval_seconds", 15.0))
        self.default_horizon_steps = int(self.config.get("default_controller_horizon_steps", 1))
        self.persistence_fallback_enabled = bool(
            self.config.get("persistence_fallback_enabled", True)
        )

        if model_override is not None:
            self.model: BaseForecaster = model_override
        else:
            self.model = instantiate_forecaster(self.model_family, self.parameters)

        self.fallback_model = PersistenceForecaster()

        # Optional per-horizon models if defined in config
        self.per_horizon_models: dict[int, BaseForecaster] = {}
        per_h = self.config.get("per_horizon_models", {})
        if isinstance(per_h, dict):
            for h_str, h_conf in per_h.items():
                try:
                    h_int = int(h_str)
                    h_fam = str(h_conf.get("model_family", "persistence"))
                    h_params = h_conf.get("parameters", {})
                    self.per_horizon_models[h_int] = instantiate_forecaster(h_fam, h_params)
                except (ValueError, TypeError):
                    continue

    @classmethod
    def from_config_file(cls, config_path: str | Path) -> "ForecastingService":
        """Instantiate service from a frozen YAML configuration file."""
        p = Path(config_path)
        if not p.exists():
            raise FileNotFoundError(f"Configuration file not found: {p}")
        raw_text = p.read_text(encoding="utf-8")
        parsed = yaml.safe_load(raw_text)
        if not isinstance(parsed, dict):
            raise ValueError(f"Configuration in {p} must parse to a dictionary")
        return cls(parsed)

    def predict(self, forecast_input: ForecastInput) -> ForecastOutput:
        """Produce point forecasts adhering to the ForecastOutput runtime contract.

        Parameters
        ----------
        forecast_input : ForecastInput
            Validated, timezone-aware, chronological sequence of observations.

        Returns
        -------
        ForecastOutput
            Type-safe forecast output containing point predictions, horizon details,
            fallback indicator, warnings, and confidence inputs.
        """
        requested_steps = forecast_input.requested_horizon_steps
        obs = forecast_input.observations
        history_count = len(obs)
        warnings: list[str] = []
        fallback_used = False

        # Select primary model for the requested horizon
        target_model = self.per_horizon_models.get(requested_steps, self.model)

        # Check if history is sufficient for target model
        if history_count < target_model.minimum_history_required:
            if self.persistence_fallback_enabled and history_count >= 1:
                fallback_used = True
                warnings.append(
                    f"Insufficient history ({history_count} < required {target_model.minimum_history_required}) "
                    f"for model '{target_model.name}'. Falling back to persistence."
                )
                active_model: BaseForecaster = self.fallback_model
            else:
                raise ValueError(
                    f"History length {history_count} is insufficient for model '{target_model.name}' "
                    f"(minimum required: {target_model.minimum_history_required}) and fallback not possible"
                )
        else:
            active_model = target_model

        # Fit model and predict
        active_model.fit(obs)
        raw_preds = active_model.predict(requested_steps)

        # Ensure documented non-negative policy
        predicted_values = [max(0.0, float(p)) for p in raw_preds]
        predicted_request_rate = predicted_values[-1]

        horizon_seconds = float(requested_steps * forecast_input.sampling_interval_seconds)

        # Compute factual confidence inputs (no fabricated probability scores)
        confidence_inputs: dict[str, Any] = {
            "history_count": history_count,
            "observations_std": float(np.std(obs)) if len(obs) > 1 else 0.0,
            "observations_recent_mean": float(np.mean(obs[-min(5, len(obs)) :])),
            "model_minimum_history": target_model.minimum_history_required,
        }
        val_summary = self.config.get("validation_metric_summary")
        if val_summary and isinstance(val_summary, dict):
            confidence_inputs["validation_metric_summary"] = val_summary

        return ForecastOutput(
            generated_at=forecast_input.generated_at,
            model_name=active_model.name,
            model_version=self.model_version,
            horizon_steps=requested_steps,
            horizon_seconds=horizon_seconds,
            predicted_values=predicted_values,
            predicted_request_rate_per_second=predicted_request_rate,
            confidence_inputs=confidence_inputs,
            history_count=history_count,
            fallback_used=fallback_used,
            warnings=warnings,
        )
