"""
StockSignal Forecasting Services — Phase 6.

Provides public dataset ingestion, feature engineering, and demand forecasting
evaluation using M5 Forecasting Accuracy and Corporación Favorita datasets.
"""
from .m5_pipeline import M5Pipeline, M5Config
from .evaluator import ForecastEvaluator, ForecastMetrics

__all__ = ["M5Pipeline", "M5Config", "ForecastEvaluator", "ForecastMetrics"]
