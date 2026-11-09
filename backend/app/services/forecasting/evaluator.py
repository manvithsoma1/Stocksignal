"""
Forecasting Evaluation Module.

Provides metrics for evaluating demand forecasting models:
- MAE (Mean Absolute Error)
- RMSE (Root Mean Squared Error)
- WAPE (Weighted Absolute Percentage Error)
- Forecast Bias
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class ForecastMetrics:
    """Container for forecasting evaluation metrics."""
    mae: float
    rmse: float
    wape: float
    bias: float
    total_actual: float
    total_forecast: float
    n_predictions: int
    
    def to_dict(self) -> dict:
        return {
            "mae": self.mae,
            "rmse": self.rmse,
            "wape": self.wape,
            "bias": self.bias,
            "total_actual": self.total_actual,
            "total_forecast": self.total_forecast,
            "n_predictions": self.n_predictions,
        }


class ForecastEvaluator:
    """
    Evaluates forecasting accuracy using standard retail metrics.
    
    Metrics:
    - MAE: Mean Absolute Error
    - RMSE: Root Mean Squared Error
    - WAPE: Weighted Absolute Percentage Error (sum of absolute errors / sum of actuals)
    - Bias: (sum of forecasts - sum of actuals) / sum of actuals
    """
    
    @staticmethod
    def calculate_metrics(
        actual: pd.Series | list[float],
        forecast: pd.Series | list[float],
    ) -> ForecastMetrics:
        """
        Calculate forecasting metrics.
        
        Args:
            actual: Actual values
            forecast: Forecasted values
            
        Returns:
            ForecastMetrics object with calculated metrics
        """
        # Convert to numpy arrays
        y_true = np.array(actual, dtype=float)
        y_pred = np.array(forecast, dtype=float)
        
        # Remove NaN values
        mask = ~(np.isnan(y_true) | np.isnan(y_pred))
        y_true = y_true[mask]
        y_pred = y_pred[mask]
        
        n = len(y_true)
        if n == 0:
            return ForecastMetrics(
                mae=float("nan"),
                rmse=float("nan"),
                wape=float("nan"),
                bias=float("nan"),
                total_actual=0.0,
                total_forecast=0.0,
                n_predictions=0,
            )
        
        # MAE
        mae = np.mean(np.abs(y_true - y_pred))
        
        # RMSE
        rmse = np.sqrt(np.mean((y_true - y_pred) ** 2))
        
        # WAPE (Weighted Absolute Percentage Error)
        total_actual = np.sum(y_true)
        if total_actual > 0:
            wape = np.sum(np.abs(y_true - y_pred)) / total_actual
        else:
            wape = float("nan")
        
        # Bias
        if total_actual > 0:
            bias = (np.sum(y_pred) - total_actual) / total_actual
        else:
            bias = float("nan")
        
        return ForecastMetrics(
            mae=float(mae),
            rmse=float(rmse),
            wape=float(wape),
            bias=float(bias),
            total_actual=float(total_actual),
            total_forecast=float(np.sum(y_pred)),
            n_predictions=n,
        )
    
    @staticmethod
    def naive_baseline(
        historical: pd.Series | list[float],
        horizon: int = 1,
    ) -> list[float]:
        """
        Generate naive baseline forecast (last value repeated).
        
        Args:
            historical: Historical time series values
            horizon: Number of periods to forecast
            
        Returns:
            List of forecasted values
        """
        if not historical:
            return [0.0] * horizon
        
        last_value = historical[-1]
        return [float(last_value)] * horizon
    
    @staticmethod
    def seasonal_naive_baseline(
        historical: pd.Series | list[float],
        seasonality: int = 7,
        horizon: int = 1,
    ) -> list[float]:
        """
        Generate seasonal naive baseline forecast (same period last season).
        
        Args:
            historical: Historical time series values
            seasonality: Seasonal period (e.g., 7 for weekly seasonality)
            horizon: Number of periods to forecast
            
        Returns:
            List of forecasted values
        """
        if len(historical) < seasonality:
            # Fall back to naive if insufficient history
            return ForecastEvaluator.naive_baseline(historical, horizon)
        
        forecasts = []
        for i in range(horizon):
            idx = len(historical) - seasonality + (i % seasonality)
            if idx >= 0:
                forecasts.append(float(historical[idx]))
            else:
                forecasts.append(0.0)
        
        return forecasts
