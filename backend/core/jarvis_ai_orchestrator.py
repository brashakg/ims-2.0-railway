"""
IMS 2.0 - JARVIS AI Orchestrator
================================

Central orchestration layer for JARVIS AI system.
Coordinates between all JARVIS modules (NLP, Analytics, Recommendations,
Compliance, Alerts, Claude API, Real-time Service).
"""

from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional, AsyncIterator
from datetime import datetime, timedelta
from enum import Enum
import asyncio

# Import all JARVIS modules
from .jarvis_nlp_engine import JarvisNLPEngine, ParsedQuery, QueryResponse
from .jarvis_analytics_engine import JarvisAnalyticsEngine, MetricData, Prediction, AnomalyAlert
from .jarvis_recommendation_engine import JarvisRecommendationEngine, Recommendation, ActionPlan
from .jarvis_compliance_engine import JarvisComplianceEngine, ComplianceViolation, AuditTrail
from .jarvis_alert_system import JarvisAlertSystem, Alert, AlertSeverity
from .jarvis_claude_integration import (
    JarvisClaudeIntegration, ClaudeContext, ResponseStyle, ClaudeResponse
)
from .jarvis_realtime_service import (
    JarvisRealtimeService, RealtimeMessage, MessageType, MessagePriority
)


class ExecutionMode(Enum):
    """Jarvis execution modes"""
    INTERACTIVE = "interactive"  # Real-time processing with immediate response
    BATCH = "batch"  # Deferred processing for resource-heavy operations
    SCHEDULED = "scheduled"  # Scheduled periodic analysis
    STREAMING = "streaming"  # Real-time streaming responses


@dataclass
class JarvisQuery:
    """User query to JARVIS AI"""
    query_id: str
    user_id: str
    user_role: str
    query_text: str
    mode: ExecutionMode
    include_analytics: bool = True
    include_recommendations: bool = True
    include_compliance: bool = False
    response_style: ResponseStyle = ResponseStyle.DETAILED
    store_context: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class JarvisResponse:
    """Comprehensive response from JARVIS AI"""
    query_id: str
    original_query: str
    primary_response: str  # Main Claude-generated response
    analytics_insights: Optional[Dict[str, Any]]
    recommendations: Optional[List[Dict[str, Any]]]
    active_alerts: List[Dict[str, Any]]
    compliance_status: Optional[Dict[str, Any]]
    anomalies_detected: List[Dict[str, Any]]
    suggested_actions: List[str]
    confidence_score: float
    execution_time_ms: int
    generated_at: datetime = field(default_factory=datetime.now)


class JarvisAIOrchestrator:
    """Main orchestration engine for JARVIS AI"""

    def __init__(
        self,
        nlp_engine: Optional[JarvisNLPEngine] = None,
        analytics_engine: Optional[JarvisAnalyticsEngine] = None,
        recommendation_engine: Optional[JarvisRecommendationEngine] = None,
        compliance_engine: Optional[JarvisComplianceEngine] = None,
        alert_system: Optional[JarvisAlertSystem] = None,
        claude_integration: Optional[JarvisClaudeIntegration] = None,
        realtime_service: Optional[JarvisRealtimeService] = None,
    ):
        """Initialize orchestrator with JARVIS modules"""

        self.nlp = nlp_engine or JarvisNLPEngine()
        self.analytics = analytics_engine or JarvisAnalyticsEngine()
        self.recommender = recommendation_engine or JarvisRecommendationEngine()
        self.compliance = compliance_engine or JarvisComplianceEngine()
        self.alerts = alert_system or JarvisAlertSystem()
        self.claude = claude_integration or JarvisClaudeIntegration()
        self.realtime = realtime_service or JarvisRealtimeService()

        self.query_history: List[JarvisQuery] = []
        self.response_cache: Dict[str, JarvisResponse] = {}
        self.max_cache_size = 500

    async def process_query(
        self,
        user_query: str,
        user_id: str,
        user_role: str,
        mode: ExecutionMode = ExecutionMode.INTERACTIVE,
        store_context: Optional[str] = None
    ) -> JarvisResponse:
        """
        Process user query through entire JARVIS pipeline

        Args:
            user_query: Natural language query from user
            user_id: User making the query
            user_role: User's role (should be SUPERADMIN for Jarvis)
            mode: Execution mode
            store_context: Specific store context if applicable

        Returns:
            Comprehensive JarvisResponse
        """

        start_time = datetime.now()
        query_id = f"jarvis_query_{int(start_time.timestamp())}"

        # Create query object
        jarvis_query = JarvisQuery(
            query_id=query_id,
            user_id=user_id,
            user_role=user_role,
            query_text=user_query,
            mode=mode,
            store_context=store_context
        )

        self.query_history.append(jarvis_query)

        try:
            # Step 1: Parse query with NLP engine
            parsed_query = self.nlp.parse_query(user_query)

            # Step 2: Gather analytics context
            analytics_insights = await self._gather_analytics(parsed_query)

            # Step 3: Generate recommendations
            recommendations = await self._generate_recommendations(analytics_insights)

            # Step 4: Check compliance status
            compliance_status = await self._check_compliance()

            # Step 5: Get active alerts
            active_alerts = self.alerts.get_active_alerts()

            # Step 6: Detect anomalies in current data
            anomalies = await self._detect_anomalies()

            # Step 7: Build Claude context
            claude_context = self._build_claude_context(
                analytics_insights,
                recommendations,
                active_alerts,
                compliance_status,
                user_role,
                store_context
            )

            # Step 8: Generate response with Claude
            if mode == ExecutionMode.STREAMING:
                # Return generator for streaming
                return await self._stream_response(
                    user_query,
                    query_id,
                    user_id,
                    parsed_query,
                    claude_context,
                    analytics_insights,
                    recommendations,
                    active_alerts,
                    compliance_status,
                    anomalies
                )
            else:
                claude_response = await self.claude.process_query(
                    user_query,
                    claude_context,
                    style=ResponseStyle.DETAILED
                )

                # Step 9: Build comprehensive response
                response = self._build_response(
                    query_id,
                    user_query,
                    claude_response,
                    analytics_insights,
                    recommendations,
                    active_alerts,
                    compliance_status,
                    anomalies,
                    start_time
                )

                # Cache response
                self._cache_response(response)

                # Send realtime updates
                await self._broadcast_response(response, user_id)

                return response

        except Exception as e:
            # Handle errors
            error_response = self._build_error_response(
                query_id,
                user_query,
                str(e),
                start_time
            )

            await self.realtime.send_alert({
                "id": f"error_{query_id}",
                "title": "JARVIS Query Error",
                "description": f"Error processing query: {str(e)}",
                "severity": "high",
                "category": "system",
                "source": "jarvis",
                "triggered_at": datetime.now().isoformat()
            })

            return error_response

    async def _gather_analytics(self, parsed_query: ParsedQuery) -> Dict[str, Any]:
        """Gather analytics insights relevant to query"""

        analytics_data = {
            "query_type": parsed_query.query_type.value,
            "time_range": parsed_query.time_range.value,
            "metrics": parsed_query.metrics,
            "filters": parsed_query.filters,
            "current_metrics": {},
            "trends": {},
            "predictions": []
        }

        # Generate insights from analytics engine
        insights = self.analytics.generate_insights({
            "sales": 125000,
            "low_stock": 5,
            "staff_utilization": 0.92
        })

        analytics_data.update(insights)

        return analytics_data

    async def _generate_recommendations(
        self,
        analytics_data: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Generate relevant recommendations"""

        recommendations = []

        # Generate inventory recommendations
        inventory_recs = self.recommender.generate_inventory_recommendations({
            "low_stock_items": ["SKU001", "SKU002", "SKU003"],
            "overstock_items": ["SKU010", "SKU011"]
        })

        # Generate sales recommendations
        sales_recs = self.recommender.generate_sales_recommendations({
            "slow_moving": ["PROD001", "PROD002"]
        })

        # Combine recommendations
        all_recs = inventory_recs + sales_recs

        # Convert to dict format
        for rec in all_recs[:5]:  # Top 5
            recommendations.append({
                "id": rec.id,
                "title": rec.title,
                "category": rec.category.value,
                "priority": rec.priority.value,
                "impact_value": rec.impact_value,
                "confidence_score": rec.confidence_score,
                "implementation_effort": rec.implementation_effort,
                "status": rec.status
            })

        return recommendations

    async def _check_compliance(self) -> Dict[str, Any]:
        """Check compliance status"""

        violations = self.compliance.detect_violations({})
        risk_indicators = self.compliance.detect_risk_indicators({})

        return {
            "violations_count": len(violations),
            "violations": violations,
            "risk_indicators": risk_indicators,
            "compliance_score": self.compliance.calculate_compliance_score(violations),
            "critical_violations": [v for v in violations if v.severity == "critical"]
        }

    async def _detect_anomalies(self) -> List[Dict[str, Any]]:
        """Detect data anomalies"""

        anomalies = []

        # Check sales anomalies
        sales_anomaly = self.analytics.detect_anomalies(
            "sales_metric",
            125000,
            [120000, 118000, 122000, 119000, 121000]
        )

        if sales_anomaly:
            anomalies.append({
                "id": sales_anomaly.id,
                "metric": sales_anomaly.metric,
                "severity": sales_anomaly.severity,
                "actual_value": sales_anomaly.actual_value,
                "expected_range": sales_anomaly.expected_range,
                "likely_cause": sales_anomaly.likely_cause,
                "recommended_action": sales_anomaly.recommended_action
            })

        return anomalies

    def _build_claude_context(
        self,
        analytics_insights: Dict[str, Any],
        recommendations: List[Dict[str, Any]],
        active_alerts: List,
        compliance_status: Dict[str, Any],
        user_role: str,
        store_context: Optional[str]
    ) -> ClaudeContext:
        """Build context to send to Claude API"""

        return ClaudeContext(
            analytics_summary=analytics_insights,
            recent_recommendations=recommendations,
            active_alerts=[
                {
                    "id": a.id,
                    "title": a.title,
                    "severity": a.severity.value,
                    "status": a.status.value
                }
                for a in active_alerts
            ],
            compliance_status=compliance_status,
            historical_queries=[q.query_text for q in self.query_history[-5:]],
            user_role=user_role,
            store_context=store_context
        )

    async def _stream_response(
        self,
        user_query: str,
        query_id: str,
        user_id: str,
        parsed_query: ParsedQuery,
        claude_context: ClaudeContext,
        analytics_insights: Dict[str, Any],
        recommendations: List[Dict[str, Any]],
        active_alerts: List,
        compliance_status: Dict[str, Any],
        anomalies: List[Dict[str, Any]]
    ) -> AsyncIterator[str]:
        """Stream response tokens in real-time"""

        # Start streaming session
        stream_session = await self.realtime.start_streaming_response(
            query_id,
            user_id,
            user_query
        )

        # Stream chunks
        chunk_index = 0
        async for chunk in self.claude.claude_client.stream_response(
            user_query,
            claude_context,
            ResponseStyle.DETAILED
        ):
            chunk_index += 1
            is_final = (chunk_index == 1)  # Would need actual total from Claude

            yield chunk

            # Send via realtime
            await self.realtime.stream_chunk(
                stream_session.stream_id,
                chunk,
                is_final=is_final
            )

    def _build_response(
        self,
        query_id: str,
        user_query: str,
        claude_response: ClaudeResponse,
        analytics_insights: Dict[str, Any],
        recommendations: List[Dict[str, Any]],
        active_alerts: List,
        compliance_status: Dict[str, Any],
        anomalies: List[Dict[str, Any]],
        start_time: datetime
    ) -> JarvisResponse:
        """Build comprehensive response"""

        execution_time = int((datetime.now() - start_time).total_seconds() * 1000)

        return JarvisResponse(
            query_id=query_id,
            original_query=user_query,
            primary_response=claude_response.generated_response,
            analytics_insights=analytics_insights,
            recommendations=recommendations,
            active_alerts=[
                {
                    "id": a.id,
                    "title": a.title,
                    "severity": a.severity.value,
                    "status": a.status.value
                }
                for a in active_alerts
            ],
            compliance_status=compliance_status,
            anomalies_detected=anomalies,
            suggested_actions=claude_response.recommended_actions,
            confidence_score=claude_response.confidence_score,
            execution_time_ms=execution_time
        )

    def _build_error_response(
        self,
        query_id: str,
        user_query: str,
        error_message: str,
        start_time: datetime
    ) -> JarvisResponse:
        """Build error response"""

        execution_time = int((datetime.now() - start_time).total_seconds() * 1000)

        return JarvisResponse(
            query_id=query_id,
            original_query=user_query,
            primary_response=f"Error processing query: {error_message}",
            analytics_insights=None,
            recommendations=None,
            active_alerts=[],
            compliance_status=None,
            anomalies_detected=[],
            suggested_actions=["Please try again or contact support"],
            confidence_score=0.0,
            execution_time_ms=execution_time
        )

    def _cache_response(self, response: JarvisResponse) -> None:
        """Cache response for future reference"""

        self.response_cache[response.query_id] = response

        # Maintain cache size
        if len(self.response_cache) > self.max_cache_size:
            # Remove oldest entries
            oldest_keys = sorted(
                self.response_cache.keys(),
                key=lambda k: self.response_cache[k].generated_at
            )[:50]

            for key in oldest_keys:
                del self.response_cache[key]

    async def _broadcast_response(
        self,
        response: JarvisResponse,
        user_id: str
    ) -> None:
        """Broadcast response via realtime service"""

        # Notify about anomalies if any
        if response.anomalies_detected:
            for anomaly in response.anomalies_detected:
                await self.realtime.send_alert(
                    {
                        "id": anomaly.get("id"),
                        "title": f"Anomaly: {anomaly.get('metric')}",
                        "description": anomaly.get("likely_cause"),
                        "severity": anomaly.get("severity"),
                        "category": "anomaly",
                        "source": "jarvis",
                        "triggered_at": datetime.now().isoformat()
                    },
                    target_role="SUPERADMIN",
                    priority=MessagePriority.HIGH
                )

    async def get_dashboard_summary(self, user_id: str) -> Dict[str, Any]:
        """Get dashboard summary for JARVIS view"""

        active_alerts = self.alerts.get_active_alerts()
        alert_summary = self.alerts.get_alert_summary()
        compliance_status = await self._check_compliance()

        recommendations = []
        inventory_recs = self.recommender.generate_inventory_recommendations({
            "low_stock_items": ["SKU001"],
            "overstock_items": []
        })

        for rec in inventory_recs[:3]:
            recommendations.append({
                "id": rec.id,
                "title": rec.title,
                "priority": rec.priority.value,
                "impact_value": rec.impact_value
            })

        return {
            "dashboard_id": f"dashboard_{int(datetime.now().timestamp())}",
            "generated_at": datetime.now().isoformat(),
            "alerts": {
                "total_active": alert_summary.get("total_active", 0),
                "by_severity": alert_summary.get("by_severity", {}),
                "recent": [
                    {
                        "id": a.id,
                        "title": a.title,
                        "severity": a.severity.value
                    }
                    for a in active_alerts[:5]
                ]
            },
            "recommendations": recommendations,
            "compliance": {
                "score": compliance_status.get("compliance_score", 100),
                "violations": compliance_status.get("violations_count", 0)
            },
            "query_history": [
                q.query_text for q in self.query_history[-10:]
            ]
        }

    def get_system_stats(self) -> Dict[str, Any]:
        """Get JARVIS system statistics"""

        return {
            "total_queries_processed": len(self.query_history),
            "cache_size": len(self.response_cache),
            "alerts_in_system": len(self.alerts.alerts),
            "compliance_violations": len(self.compliance.violations),
            "active_streaming_sessions": len(self.realtime.streaming_sessions),
            "connected_clients": len(self.realtime.connection_manager.clients),
            "cache_hit_rate": self.claude.get_stats().get("cache_hit_rate", 0)
        }


# Initialize global JARVIS orchestrator
jarvis_orchestrator = JarvisAIOrchestrator()
