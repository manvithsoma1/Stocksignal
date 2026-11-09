"""
Tests for Phase 6 Forecasting Module.

Tests:
- ForecastEvaluator metrics calculation
- Naive baseline generation
- Seasonal naive baseline generation
- M5Pipeline configuration
- M5Pipeline file validation
"""
from __future__ import annotations

import pytest
import pandas as pd
import numpy as np

from app.services.forecasting.evaluator import ForecastEvaluator, ForecastMetrics
from app.services.forecasting.m5_pipeline import M5Config, M5Pipeline


class TestForecastEvaluator:
    """Tests for ForecastEvaluator metrics calculation."""
    
    def test_calculate_metrics_basic(self):
        """Test basic metrics calculation with simple data."""
        actual = [10, 20, 30, 40, 50]
        forecast = [12, 18, 32, 38, 52]
        
        metrics = ForecastEvaluator.calculate_metrics(actual, forecast)
        
        assert metrics.n_predictions == 5
        assert metrics.total_actual == 150.0
        assert metrics.total_forecast == 152.0
        assert metrics.mae > 0
        assert metrics.rmse > 0
        assert metrics.wape > 0
        assert abs(metrics.bias) < 1.0  # Bias should be small
    
    def test_calculate_metrics_perfect_forecast(self):
        """Test metrics when forecast equals actual."""
        actual = [10, 20, 30, 40, 50]
        forecast = [10, 20, 30, 40, 50]
        
        metrics = ForecastEvaluator.calculate_metrics(actual, forecast)
        
        assert metrics.mae == 0.0
        assert metrics.rmse == 0.0
        assert metrics.wape == 0.0
        assert metrics.bias == 0.0
    
    def test_calculate_metrics_with_nan(self):
        """Test that NaN values are handled correctly."""
        actual = [10, np.nan, 30, 40, 50]
        forecast = [12, 18, 32, np.nan, 52]
        
        metrics = ForecastEvaluator.calculate_metrics(actual, forecast)
        
        # Should only count non-NaN pairs
        assert metrics.n_predictions == 3  # Only 3 valid pairs
    
    def test_naive_baseline(self):
        """Test naive baseline forecast."""
        historical = [10, 15, 20, 25, 30]
        
        forecast = ForecastEvaluator.naive_baseline(historical, horizon=3)
        
        assert len(forecast) == 3
        assert all(f == 30.0 for f in forecast)
    
    def test_naive_baseline_empty(self):
        """Test naive baseline with empty history."""
        forecast = ForecastEvaluator.naive_baseline([], horizon=3)
        
        assert len(forecast) == 3
        assert all(f == 0.0 for f in forecast)
    
    def test_seasonal_naive_baseline(self):
        """Test seasonal naive baseline."""
        historical = [10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75]
        
        forecast = ForecastEvaluator.seasonal_naive_baseline(
            historical, seasonality=7, horizon=2
        )
        
        assert len(forecast) == 2
        # Should take values from 7 days ago
        assert forecast[0] == 45.0  # 14 - 7 = 7th element
        assert forecast[1] == 50.0  # 15 - 7 = 8th element
    
    def test_seasonal_naive_fallback(self):
        """Test seasonal naive falls back to naive when insufficient history."""
        historical = [10, 15, 20]
        
        forecast = ForecastEvaluator.seasonal_naive_baseline(
            historical, seasonality=7, horizon=2
        )
        
        # Should fall back to naive
        assert all(f == 20.0 for f in forecast)


class TestM5Config:
    """Tests for M5Config."""
    
    def test_default_config(self):
        """Test default configuration values."""
        config = M5Config()
        
        assert config.data_dir == "data/m5"
        assert config.sales_file == "sales_train_validation.csv"
        assert config.calendar_file == "calendar.csv"
        assert config.prices_file == "sell_prices.csv"
        assert config.lag_days == [1, 7, 28]
        assert config.rolling_windows == [7, 14, 28]
        assert config.forecast_horizon == 28
        assert config.use_sample is False
    
    def test_custom_config(self):
        """Test custom configuration."""
        config = M5Config(
            data_dir="custom/path",
            use_sample=True,
            sample_n_items=50,
        )
        
        assert config.data_dir == "custom/path"
        assert config.use_sample is True
        assert config.sample_n_items == 50


class TestM5Pipeline:
    """Tests for M5Pipeline."""
    
    def test_validate_data_files_missing(self, tmp_path):
        """Test file validation when files don't exist."""
        config = M5Config(data_dir=str(tmp_path))
        pipeline = M5Pipeline(config)
        
        status = pipeline.validate_data_files()
        
        # All files should be missing
        assert not any(status.values())
    
    def test_pipeline_initialization(self):
        """Test pipeline initialization."""
        config = M5Config()
        pipeline = M5Pipeline(config)
        
        assert pipeline.cfg == config
        assert pipeline.data_dir is not None
