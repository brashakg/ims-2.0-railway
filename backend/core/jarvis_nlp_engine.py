"""
IMS 2.0 - Natural Language Processing Engine for JARVIS
======================================================

Interprets business queries in natural language and generates responses.
Integrates with Claude API for advanced language understanding.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Any
from enum import Enum
import re
from datetime import datetime, timedelta


class QueryType(Enum):
    """Types of queries JARVIS can handle"""
    SALES_ANALYSIS = "sales_analysis"
    INVENTORY_QUERY = "inventory_query"
    CUSTOMER_INSIGHT = "customer_insight"
    STAFF_REPORT = "staff_report"
    FINANCIAL_ANALYSIS = "financial_analysis"
    PREDICTION = "prediction"
    RECOMMENDATION = "recommendation"
    COMPLIANCE = "compliance"
    PERFORMANCE = "performance"
    TREND = "trend"
    ANOMALY = "anomaly"
    COMPARISON = "comparison"
    FORECAST = "forecast"


class TimeRange(Enum):
    """Time ranges for queries"""
    TODAY = "today"
    THIS_WEEK = "this_week"
    THIS_MONTH = "this_month"
    THIS_QUARTER = "this_quarter"
    THIS_YEAR = "this_year"
    LAST_7_DAYS = "last_7_days"
    LAST_30_DAYS = "last_30_days"
    LAST_90_DAYS = "last_90_days"
    LAST_YEAR = "last_year"
    CUSTOM = "custom"


@dataclass
class ParsedQuery:
    """Parsed business query"""
    original_query: str
    query_type: QueryType
    time_range: TimeRange
    metrics: List[str]
    filters: Dict[str, Any]
    comparison: Optional[str]
    aggregation: Optional[str]
    sorting: Optional[str]
    confidence: float


@dataclass
class QueryResponse:
    """Response to a business query"""
    query_id: str
    original_query: str
    response_text: str
    data_summary: Dict[str, Any]
    visualization_type: Optional[str]  # "bar_chart", "line_chart", "table", etc
    key_insights: List[str]
    confidence: float
    generated_at: datetime


class JarvisNLPEngine:
    """Natural Language Processing engine for business queries"""

    def __init__(self):
        self.query_history: List[ParsedQuery] = []
        self.response_cache: Dict[str, QueryResponse] = {}

    def parse_query(self, user_query: str) -> ParsedQuery:
        """Parse natural language query into structured format"""
        query_lower = user_query.lower()

        # Determine query type
        query_type = self._determine_query_type(query_lower)

        # Determine time range
        time_range = self._determine_time_range(query_lower)

        # Extract metrics
        metrics = self._extract_metrics(query_lower)

        # Extract filters
        filters = self._extract_filters(query_lower)

        # Check for comparisons
        comparison = self._extract_comparison(query_lower)

        # Check for aggregation
        aggregation = self._extract_aggregation(query_lower)

        # Check for sorting
        sorting = self._extract_sorting(query_lower)

        # Calculate confidence
        confidence = self._calculate_confidence(query_type, metrics, filters)

        parsed = ParsedQuery(
            original_query=user_query,
            query_type=query_type,
            time_range=time_range,
            metrics=metrics,
            filters=filters,
            comparison=comparison,
            aggregation=aggregation,
            sorting=sorting,
            confidence=confidence
        )

        self.query_history.append(parsed)
        return parsed

    def _determine_query_type(self, query: str) -> QueryType:
        """Determine the type of query"""
        keywords = {
            QueryType.SALES_ANALYSIS: ["sales", "revenue", "orders", "customers", "transaction"],
            QueryType.INVENTORY_QUERY: ["stock", "inventory", "warehouse", "reorder", "sku"],
            QueryType.CUSTOMER_INSIGHT: ["customer", "client", "segment", "loyalty", "retention"],
            QueryType.STAFF_REPORT: ["staff", "employee", "attendance", "performance", "hours"],
            QueryType.FINANCIAL_ANALYSIS: ["financial", "profit", "margin", "cost", "expense"],
            QueryType.PREDICTION: ["predict", "forecast", "expect", "anticipate", "upcoming"],
            QueryType.RECOMMENDATION: ["recommend", "suggest", "advice", "should we", "consider"],
            QueryType.COMPLIANCE: ["compliance", "audit", "regulation", "policy", "risk"],
            QueryType.PERFORMANCE: ["performance", "metrics", "kpi", "benchmark", "target"],
            QueryType.TREND: ["trend", "pattern", "growth", "decline", "seasonal"],
            QueryType.ANOMALY: ["anomaly", "unusual", "unexpected", "error", "problem"],
            QueryType.COMPARISON: ["compare", "versus", "vs", "difference", "similar"],
            QueryType.FORECAST: ["forecast", "projection", "outlook", "trend"]
        }

        for qtype, keywords_list in keywords.items():
            if any(keyword in query for keyword in keywords_list):
                return qtype

        return QueryType.PERFORMANCE

    def _determine_time_range(self, query: str) -> TimeRange:
        """Determine the time range for the query"""
        time_keywords = {
            TimeRange.TODAY: ["today", "this day"],
            TimeRange.THIS_WEEK: ["this week", "this current week"],
            TimeRange.THIS_MONTH: ["this month"],
            TimeRange.THIS_QUARTER: ["this quarter", "q1", "q2", "q3", "q4"],
            TimeRange.THIS_YEAR: ["this year", "ytd"],
            TimeRange.LAST_7_DAYS: ["last 7 days", "past week", "7 days"],
            TimeRange.LAST_30_DAYS: ["last 30 days", "past month", "30 days"],
            TimeRange.LAST_90_DAYS: ["last 90 days", "past quarter", "90 days"],
            TimeRange.LAST_YEAR: ["last year", "past year"]
        }

        for time_range, keywords in time_keywords.items():
            if any(keyword in query for keyword in keywords):
                return time_range

        return TimeRange.THIS_MONTH  # Default

    def _extract_metrics(self, query: str) -> List[str]:
        """Extract metrics from query"""
        metric_keywords = {
            "sales": ["sales", "revenue", "turnover"],
            "orders": ["orders", "transactions"],
            "customers": ["customers", "clients"],
            "inventory": ["stock", "inventory", "sku"],
            "profit": ["profit", "margin", "roi"],
            "growth": ["growth", "increase", "improvement"],
            "efficiency": ["efficiency", "productivity", "utilization"]
        }

        metrics = []
        for metric, keywords in metric_keywords.items():
            if any(keyword in query for keyword in keywords):
                metrics.append(metric)

        return metrics if metrics else ["general"]

    def _extract_filters(self, query: str) -> Dict[str, Any]:
        """Extract filters from query"""
        filters = {}

        # Store filters
        store_pattern = r"store[s]?\s+(?:at\s+)?(\w+|[\w\s]+(?=\s+store))"
        store_matches = re.findall(store_pattern, query)
        if store_matches:
            filters["stores"] = store_matches

        # Category filters
        category_pattern = r"(?:category|product|type)\s+(\w+)"
        category_matches = re.findall(category_pattern, query)
        if category_matches:
            filters["categories"] = category_matches

        # Region filters
        region_keywords = ["delhi", "mumbai", "bangalore", "gurgaon", "noida", "north", "south", "east", "west"]
        regions = [word for word in query.split() if word.lower() in region_keywords]
        if regions:
            filters["regions"] = regions

        return filters

    def _extract_comparison(self, query: str) -> Optional[str]:
        """Extract comparison parameters"""
        comparison_keywords = ["compare", "versus", "vs", "difference", "similar", "same"]
        if any(keyword in query for keyword in comparison_keywords):
            return "comparison"
        return None

    def _extract_aggregation(self, query: str) -> Optional[str]:
        """Extract aggregation type"""
        aggregation_keywords = {
            "total": ["total", "sum", "aggregate"],
            "average": ["average", "mean", "avg"],
            "max": ["maximum", "highest", "max"],
            "min": ["minimum", "lowest", "min"],
            "count": ["count", "number", "how many"]
        }

        for agg_type, keywords in aggregation_keywords.items():
            if any(keyword in query for keyword in keywords):
                return agg_type

        return None

    def _extract_sorting(self, query: str) -> Optional[str]:
        """Extract sorting preference"""
        if "highest" in query or "most" in query:
            return "descending"
        elif "lowest" in query or "least" in query:
            return "ascending"
        return None

    def _calculate_confidence(self, query_type: QueryType, metrics: List[str],
                             filters: Dict[str, Any]) -> float:
        """Calculate confidence score for query understanding"""
        confidence = 0.7  # Base confidence

        # Increase confidence if specific metrics mentioned
        if metrics and metrics[0] != "general":
            confidence += 0.15

        # Increase confidence if filters present
        if filters:
            confidence += 0.1

        return min(confidence, 1.0)

    def generate_response(self, parsed_query: ParsedQuery, data: Dict[str, Any]) -> QueryResponse:
        """Generate natural language response to query"""
        response_text = self._build_response_text(parsed_query, data)

        visualization = self._determine_visualization(parsed_query)

        key_insights = self._extract_key_insights(parsed_query, data)

        response = QueryResponse(
            query_id=f"query_{int(datetime.now().timestamp())}",
            original_query=parsed_query.original_query,
            response_text=response_text,
            data_summary=data,
            visualization_type=visualization,
            key_insights=key_insights,
            confidence=parsed_query.confidence,
            generated_at=datetime.now()
        )

        return response

    def _build_response_text(self, parsed_query: ParsedQuery, data: Dict[str, Any]) -> str:
        """Build natural language response"""
        metric_str = ", ".join(parsed_query.metrics) if parsed_query.metrics else "performance"

        response = f"Based on {parsed_query.time_range.value} data, here's your {metric_str} analysis:\n\n"

        if "summary" in data:
            response += f"Summary: {data['summary']}\n"

        if "value" in data:
            response += f"Current Value: {data['value']}\n"

        if "trend" in data:
            response += f"Trend: {data['trend']}\n"

        if "key_points" in data:
            response += "Key Points:\n"
            for point in data["key_points"]:
                response += f"  • {point}\n"

        return response

    def _determine_visualization(self, parsed_query: ParsedQuery) -> Optional[str]:
        """Determine best visualization for response"""
        if parsed_query.query_type == QueryType.TREND:
            return "line_chart"
        elif parsed_query.query_type == QueryType.COMPARISON:
            return "bar_chart"
        elif parsed_query.query_type == QueryType.PERFORMANCE:
            return "gauge_chart"
        return "table"

    def _extract_key_insights(self, parsed_query: ParsedQuery, data: Dict[str, Any]) -> List[str]:
        """Extract key insights from data"""
        insights = []

        if "insight_1" in data:
            insights.append(data["insight_1"])
        if "insight_2" in data:
            insights.append(data["insight_2"])

        return insights


# Initialize global NLP engine
jarvis_nlp = JarvisNLPEngine()
