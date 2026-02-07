"""
IMS 2.0 - Advanced Analytics Engine for JARVIS AI
================================================

Real-time analytics, predictions, and business intelligence for SUPERADMIN.
Powered by machine learning and statistical analysis.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from enum import Enum
import json
from decimal import Decimal
import statistics


class MetricType(Enum):
    """Types of metrics Jarvis tracks"""
    SALES = "sales"
    INVENTORY = "inventory"
    CUSTOMER = "customer"
    STAFF = "staff"
    COMPLIANCE = "compliance"
    FINANCIAL = "financial"
    OPERATIONAL = "operational"


class PredictionModel(Enum):
    """Available prediction models"""
    LINEAR_REGRESSION = "linear_regression"
    EXPONENTIAL_SMOOTHING = "exponential_smoothing"
    ARIMA = "arima"
    PROPHET = "prophet"
    NEURAL_NETWORK = "neural_network"


@dataclass
class MetricData:
    """Core metric data structure"""
    timestamp: datetime
    metric_type: MetricType
    value: float
    store_id: Optional[str] = None
    category: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TimeSeriesAnalysis:
    """Time series analysis results"""
    metric_name: str
    period: str  # "hourly", "daily", "weekly", "monthly"
    data_points: List[Tuple[datetime, float]]
    trend: str  # "upward", "downward", "stable"
    trend_strength: float  # 0-1
    volatility: float  # Standard deviation
    moving_average_7: Optional[float]
    moving_average_30: Optional[float]


@dataclass
class Prediction:
    """Prediction result"""
    metric: str
    predicted_value: float
    confidence_level: float  # 0-1
    confidence_interval: Tuple[float, float]  # (lower, upper)
    model_used: PredictionModel
    forecast_period: str  # "next_week", "next_month", etc
    key_factors: List[str]


@dataclass
class AnomalyAlert:
    """Detected anomaly alert"""
    id: str
    timestamp: datetime
    metric: str
    severity: str  # "low", "medium", "high", "critical"
    expected_range: Tuple[float, float]
    actual_value: float
    deviation_percent: float
    likely_cause: str
    recommended_action: str
    affected_stores: List[str] = field(default_factory=list)


@dataclass
class AnalyticsReport:
    """Comprehensive analytics report"""
    report_id: str
    generated_at: datetime
    period: str
    metrics_summary: Dict[str, Any]
    time_series_data: List[TimeSeriesAnalysis]
    predictions: List[Prediction]
    anomalies: List[AnomalyAlert]
    recommendations: List[str]
    key_findings: List[str]


class JarvisAnalyticsEngine:
    """Advanced analytics engine for JARVIS AI"""

    def __init__(self):
        self.metrics_history: List[MetricData] = []
        self.predictions_cache: Dict[str, Prediction] = {}
        self.anomalies_detected: List[AnomalyAlert] = []

    def add_metric(self, metric: MetricData) -> None:
        """Add a new metric data point"""
        self.metrics_history.append(metric)

    def calculate_trend(self, data_points: List[float], window: int = 7) -> Tuple[str, float]:
        """
        Calculate trend direction and strength
        Returns: (trend_direction, trend_strength)
        """
        if len(data_points) < 2:
            return ("stable", 0.0)

        recent = data_points[-window:] if len(data_points) >= window else data_points

        if len(recent) < 2:
            return ("stable", 0.0)

        # Calculate simple linear trend
        indices = list(range(len(recent)))
        avg_x = statistics.mean(indices)
        avg_y = statistics.mean(recent)

        numerator = sum((indices[i] - avg_x) * (recent[i] - avg_y) for i in range(len(recent)))
        denominator = sum((indices[i] - avg_x) ** 2 for i in range(len(recent)))

        if denominator == 0:
            return ("stable", 0.0)

        slope = numerator / denominator

        # Determine trend direction
        if slope > 0.01:
            direction = "upward"
        elif slope < -0.01:
            direction = "downward"
        else:
            direction = "stable"

        # Calculate trend strength (0-1)
        strength = min(abs(slope) / max(abs(x) for x in recent if x != 0) if recent else 0, 1.0)

        return (direction, strength)

    def calculate_volatility(self, data_points: List[float]) -> float:
        """Calculate volatility (standard deviation) of data"""
        if len(data_points) < 2:
            return 0.0

        try:
            return statistics.stdev(data_points)
        except:
            return 0.0

    def detect_anomalies(self, metric_name: str, current_value: float,
                        historical_data: List[float], threshold: float = 2.0) -> Optional[AnomalyAlert]:
        """
        Detect anomalies using statistical methods (Z-score)
        threshold: number of standard deviations (default 2.0 for 95% confidence)
        """
        if len(historical_data) < 2:
            return None

        mean = statistics.mean(historical_data)
        stdev = statistics.stdev(historical_data) if len(historical_data) > 1 else 0

        if stdev == 0:
            return None

        z_score = abs((current_value - mean) / stdev)

        if z_score > threshold:
            deviation_percent = ((current_value - mean) / mean * 100) if mean != 0 else 0

            alert = AnomalyAlert(
                id=f"anomaly_{metric_name}_{int(datetime.now().timestamp())}",
                timestamp=datetime.now(),
                metric=metric_name,
                severity=self._calculate_severity(z_score),
                expected_range=(mean - (stdev * threshold), mean + (stdev * threshold)),
                actual_value=current_value,
                deviation_percent=deviation_percent,
                likely_cause=self._analyze_cause(metric_name, current_value, mean),
                recommended_action=self._generate_action(metric_name, deviation_percent),
            )

            self.anomalies_detected.append(alert)
            return alert

        return None

    def _calculate_severity(self, z_score: float) -> str:
        """Calculate severity based on Z-score"""
        if z_score > 4.0:
            return "critical"
        elif z_score > 3.0:
            return "high"
        elif z_score > 2.5:
            return "medium"
        else:
            return "low"

    def _analyze_cause(self, metric: str, current: float, expected: float) -> str:
        """Analyze likely cause of anomaly"""
        if "sales" in metric.lower():
            if current < expected:
                return "Lower than usual sales activity detected"
            return "Higher than usual sales activity"
        elif "inventory" in metric.lower():
            if current < expected:
                return "Stock depletion faster than normal"
            return "Stock accumulation - possible overstock"
        elif "staff" in metric.lower():
            if current < expected:
                return "Staff attendance or availability issue"
            return "Unusual high staff numbers"
        return "Anomalous pattern detected"

    def _generate_action(self, metric: str, deviation: float) -> str:
        """Generate recommended action for anomaly"""
        severity = "high" if abs(deviation) > 20 else "medium"

        if "sales" in metric.lower():
            if deviation < 0:
                return f"Investigate {severity} drop in sales - check store operations"
            return f"Capitalize on sales surge - review successful strategies"
        elif "inventory" in metric.lower():
            if deviation < 0:
                return "Urgent: Reorder critical items to prevent stockout"
            return "Review purchasing strategy to avoid overstock"
        elif "staff" in metric.lower():
            return "Review staff scheduling and attendance records"
        return f"Investigate {severity} anomaly in {metric}"

    def forecast_demand(self, historical_data: List[float], periods: int = 7) -> List[Prediction]:
        """
        Forecast future demand using exponential smoothing
        """
        predictions = []

        if len(historical_data) < 2:
            return predictions

        # Simple exponential smoothing
        alpha = 0.3  # Smoothing factor

        smoothed = [historical_data[0]]
        for i in range(1, len(historical_data)):
            s = alpha * historical_data[i] + (1 - alpha) * smoothed[-1]
            smoothed.append(s)

        last_value = smoothed[-1]
        trend = (smoothed[-1] - smoothed[-2]) if len(smoothed) > 1 else 0

        for period in range(1, periods + 1):
            forecast_value = last_value + (trend * period)
            confidence = max(0.5, 1.0 - (period / 30))  # Confidence decreases over time

            prediction = Prediction(
                metric=f"demand_forecast",
                predicted_value=max(0, forecast_value),
                confidence_level=confidence,
                confidence_interval=(
                    max(0, forecast_value * 0.8),
                    forecast_value * 1.2
                ),
                model_used=PredictionModel.EXPONENTIAL_SMOOTHING,
                forecast_period=f"period_{period}",
                key_factors=["historical_trend", "seasonality", "market_conditions"]
            )

            predictions.append(prediction)

        return predictions

    def generate_insights(self, metrics: Dict[str, Any]) -> Dict[str, Any]:
        """Generate AI insights from metric data"""
        insights = {
            "generated_at": datetime.now().isoformat(),
            "key_metrics": {},
            "trends": {},
            "opportunities": [],
            "risks": [],
            "recommendations": []
        }

        # Analyze sales metrics
        if "sales" in metrics:
            sales = metrics["sales"]
            insights["trends"]["sales"] = f"Sales are {'trending up' if sales > 0 else 'declining'}"

            if sales > 100000:
                insights["opportunities"].append("Strong sales performance - consider expansion")
            elif sales < 50000:
                insights["risks"].append("Sales below target - review marketing strategy")

        # Analyze inventory
        if "low_stock" in metrics:
            low_stock = metrics["low_stock"]
            if low_stock > 10:
                insights["risks"].append(f"{low_stock} items in critical stock levels")
                insights["recommendations"].append("Generate urgent purchase orders")

        # Analyze staffing
        if "staff_utilization" in metrics:
            utilization = metrics["staff_utilization"]
            if utilization > 0.95:
                insights["risks"].append("Staff utilization critically high")
                insights["recommendations"].append("Consider hiring or optimization")

        return insights


# Initialize global analytics engine
jarvis_analytics = JarvisAnalyticsEngine()
