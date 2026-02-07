"""
IMS 2.0 - Claude API Integration for JARVIS AI
==============================================

Integrates Claude API to provide advanced natural language understanding
and generation for the JARVIS AI system. Enables intelligent responses to
business queries with context from analytics and recommendations.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, AsyncIterator
from enum import Enum
from datetime import datetime
import asyncio
import json
from abc import ABC, abstractmethod


class ResponseStyle(Enum):
    """Response generation styles"""
    CONCISE = "concise"  # Brief, direct answers
    DETAILED = "detailed"  # Comprehensive analysis
    EXECUTIVE = "executive"  # High-level summary for executives
    TECHNICAL = "technical"  # In-depth technical details
    ACTIONABLE = "actionable"  # Focus on specific actions


@dataclass
class ConversationMessage:
    """Message in the conversation history"""
    role: str  # "user" or "assistant"
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    tokens_used: int = 0


@dataclass
class ClaudeContext:
    """Context to pass to Claude API"""
    analytics_summary: Dict[str, Any]  # From JarvisAnalyticsEngine
    recent_recommendations: List[Dict[str, Any]]  # Top 5 recommendations
    active_alerts: List[Dict[str, Any]]  # Current active alerts
    compliance_status: Dict[str, Any]  # From compliance engine
    historical_queries: List[str]  # Previous similar queries
    user_role: str  # SUPERADMIN
    store_context: Optional[str] = None  # Specific store if applicable


@dataclass
class ClaudeResponse:
    """Response from Claude API"""
    response_id: str
    original_query: str
    generated_response: str
    response_style: ResponseStyle
    confidence_score: float  # 0-1
    relevant_sections: List[str]  # Which parts of context were used
    recommended_actions: List[str]  # Specific actions Claude recommends
    follow_up_questions: List[str]  # Suggested next questions
    tokens_used: int
    latency_ms: int
    generated_at: datetime = field(default_factory=datetime.now)
    is_streaming: bool = False


class ClaudeAPIClient(ABC):
    """Abstract base for Claude API client"""

    @abstractmethod
    async def generate_response(
        self,
        query: str,
        context: ClaudeContext,
        style: ResponseStyle = ResponseStyle.DETAILED
    ) -> ClaudeResponse:
        """Generate response using Claude API"""
        pass

    @abstractmethod
    async def stream_response(
        self,
        query: str,
        context: ClaudeContext,
        style: ResponseStyle = ResponseStyle.DETAILED
    ) -> AsyncIterator[str]:
        """Stream response tokens in real-time"""
        pass


class MockClaudeAPIClient(ClaudeAPIClient):
    """Mock Claude API client for testing (to be replaced with real API)"""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
        self.conversation_history: List[ConversationMessage] = []
        self.response_cache: Dict[str, ClaudeResponse] = {}

    async def generate_response(
        self,
        query: str,
        context: ClaudeContext,
        style: ResponseStyle = ResponseStyle.DETAILED
    ) -> ClaudeResponse:
        """Generate response (mock implementation)"""

        # Check cache
        cache_key = f"{query}:{style.value}"
        if cache_key in self.response_cache:
            return self.response_cache[cache_key]

        # Simulate Claude analysis
        response_text = self._generate_mock_response(query, context, style)

        response = ClaudeResponse(
            response_id=f"claude_{int(datetime.now().timestamp())}",
            original_query=query,
            generated_response=response_text,
            response_style=style,
            confidence_score=0.92,
            relevant_sections=self._extract_relevant_sections(context),
            recommended_actions=self._extract_actions(query, context),
            follow_up_questions=self._generate_follow_ups(query),
            tokens_used=150,
            latency_ms=250,
            is_streaming=False
        )

        # Cache response
        self.response_cache[cache_key] = response

        # Add to conversation history
        self.conversation_history.append(
            ConversationMessage(role="user", content=query)
        )
        self.conversation_history.append(
            ConversationMessage(role="assistant", content=response_text)
        )

        return response

    async def stream_response(
        self,
        query: str,
        context: ClaudeContext,
        style: ResponseStyle = ResponseStyle.DETAILED
    ) -> AsyncIterator[str]:
        """Stream response tokens (mock implementation)"""

        response_text = self._generate_mock_response(query, context, style)

        # Simulate token-by-token streaming
        words = response_text.split()
        for word in words:
            yield word + " "
            await asyncio.sleep(0.05)  # Simulate token latency

    def _generate_mock_response(
        self,
        query: str,
        context: ClaudeContext,
        style: ResponseStyle
    ) -> str:
        """Generate mock response based on query and context"""

        query_lower = query.lower()

        # Sales analysis
        if "sales" in query_lower:
            base = "Based on current data analysis, sales performance shows"
            if context.analytics_summary.get("sales_trend") == "upward":
                base += " strong growth momentum. "
                base += "Key drivers include improved marketing effectiveness and seasonal demand."
            else:
                base += " declining trends. "
                base += "Recommend reviewing promotional strategies and competitive positioning."

            if context.recent_recommendations:
                top_rec = context.recent_recommendations[0]
                base += f"\n\nTop priority: {top_rec.get('title', 'Sales optimization')}"

            return base

        # Inventory analysis
        elif "inventory" in query_lower or "stock" in query_lower:
            low_stock_count = 0
            if context.analytics_summary.get("low_stock_items"):
                low_stock_count = len(context.analytics_summary["low_stock_items"])

            base = f"Current inventory analysis reveals {low_stock_count} critical stock items. "
            base += "Recommend immediate reorder to prevent stockouts. "
            base += "Suggest implementing automated reorder points based on demand forecasting."

            return base

        # Compliance analysis
        elif "compliance" in query_lower or "risk" in query_lower:
            violations = context.compliance_status.get("violations_count", 0)
            compliance_score = context.compliance_status.get("compliance_score", 100)

            base = f"Compliance score: {compliance_score}/100. "
            if violations > 0:
                base += f"{violations} violations detected requiring attention. "
                base += "Prioritize GST filing and audit documentation."
            else:
                base += "Strong compliance posture maintained."

            return base

        # Recommendations analysis
        elif "recommend" in query_lower:
            base = "Based on comprehensive analysis, here are priority recommendations:\n"

            if context.recent_recommendations:
                for i, rec in enumerate(context.recent_recommendations[:3], 1):
                    base += f"\n{i}. {rec.get('title', 'Recommendation')} "
                    base += f"(Impact: ₹{rec.get('impact_value', 0):,.0f})"

            return base

        # Default response
        else:
            base = f"Analyzing your query: '{query}'\n\n"
            base += "Current system status: All modules operational. "
            base += f"Active alerts: {len(context.active_alerts)}. "
            base += f"Pending recommendations: {len(context.recent_recommendations)}."

            return base

    def _extract_relevant_sections(self, context: ClaudeContext) -> List[str]:
        """Extract which sections of context were used"""
        sections = []

        if context.analytics_summary:
            sections.append("Analytics Summary")
        if context.recent_recommendations:
            sections.append("Recent Recommendations")
        if context.active_alerts:
            sections.append("Active Alerts")
        if context.compliance_status:
            sections.append("Compliance Status")

        return sections

    def _extract_actions(self, query: str, context: ClaudeContext) -> List[str]:
        """Extract recommended actions"""
        actions = []

        query_lower = query.lower()

        if "sales" in query_lower:
            actions.append("Review promotional effectiveness")
            actions.append("Analyze competitor pricing")

        if "inventory" in query_lower or "stock" in query_lower:
            actions.append("Generate purchase orders for critical items")
            actions.append("Update reorder thresholds")

        if "compliance" in query_lower:
            actions.append("Schedule compliance audit")
            actions.append("Review documentation")

        if not actions:
            actions.append("Schedule performance review")

        return actions

    def _generate_follow_ups(self, query: str) -> List[str]:
        """Generate suggested follow-up questions"""

        follow_ups = []
        query_lower = query.lower()

        if "sales" in query_lower:
            follow_ups.append("What are the top-performing product categories?")
            follow_ups.append("How do store locations compare in sales?")
            follow_ups.append("What seasonal patterns are visible?")

        elif "inventory" in query_lower:
            follow_ups.append("What's the inventory turnover rate?")
            follow_ups.append("Which suppliers are most reliable?")
            follow_ups.append("What's the reorder cost optimization?")

        elif "compliance" in query_lower:
            follow_ups.append("What violations need immediate attention?")
            follow_ups.append("What's the compliance improvement timeline?")
            follow_ups.append("Are there process gaps to address?")

        else:
            follow_ups.append("What specific metrics interest you?")
            follow_ups.append("Should we drill down into specific stores?")
            follow_ups.append("Would you like historical comparisons?")

        return follow_ups


class JarvisClaudeIntegration:
    """Main integration layer for Claude API with JARVIS"""

    def __init__(self, api_client: Optional[ClaudeAPIClient] = None):
        self.claude_client = api_client or MockClaudeAPIClient()
        self.conversation_history: List[ConversationMessage] = []
        self.query_cache: Dict[str, ClaudeResponse] = {}
        self.max_history_length = 50  # Keep last 50 messages

    async def process_query(
        self,
        query: str,
        context: ClaudeContext,
        style: ResponseStyle = ResponseStyle.DETAILED,
        use_streaming: bool = False
    ) -> ClaudeResponse | AsyncIterator[str]:
        """
        Process a query through Claude API

        Args:
            query: User's natural language query
            context: Business context (analytics, alerts, etc.)
            style: Response generation style
            use_streaming: Whether to stream response tokens

        Returns:
            Either ClaudeResponse or AsyncIterator for streaming
        """

        # Check cache for identical queries
        cache_key = f"{query}:{style.value}"
        if cache_key in self.query_cache:
            cached_response = self.query_cache[cache_key]
            cached_response.is_streaming = use_streaming
            return cached_response

        # Add query to history
        self.conversation_history.append(
            ConversationMessage(role="user", content=query)
        )

        # Generate response
        if use_streaming:
            return await self.claude_client.stream_response(query, context, style)
        else:
            response = await self.claude_client.generate_response(query, context, style)

            # Cache response
            self.query_cache[cache_key] = response

            # Add to history
            self.conversation_history.append(
                ConversationMessage(role="assistant", content=response.generated_response)
            )

            # Trim history if needed
            if len(self.conversation_history) > self.max_history_length:
                self.conversation_history = self.conversation_history[-self.max_history_length:]

            return response

    async def generate_report(
        self,
        report_type: str,
        context: ClaudeContext
    ) -> str:
        """Generate executive report using Claude"""

        query = f"Generate a {report_type} report based on current data"
        response = await self.claude_client.generate_response(
            query,
            context,
            ResponseStyle.EXECUTIVE
        )

        return response.generated_response

    async def analyze_anomaly(
        self,
        anomaly_details: Dict[str, Any],
        context: ClaudeContext
    ) -> Dict[str, Any]:
        """Analyze anomaly and provide detailed insights"""

        query = (
            f"Analyze this anomaly: {anomaly_details.get('metric')} "
            f"is {anomaly_details.get('severity')}. "
            f"Actual: {anomaly_details.get('actual_value')}, "
            f"Expected: {anomaly_details.get('expected_value')}. "
            f"What should we do?"
        )

        response = await self.claude_client.generate_response(
            query,
            context,
            ResponseStyle.ACTIONABLE
        )

        return {
            "analysis": response.generated_response,
            "recommended_actions": response.recommended_actions,
            "confidence": response.confidence_score
        }

    async def validate_recommendation(
        self,
        recommendation: Dict[str, Any],
        context: ClaudeContext
    ) -> Dict[str, Any]:
        """Validate and enhance recommendation with Claude insights"""

        query = (
            f"Evaluate this recommendation: {recommendation.get('title')}. "
            f"Impact: ₹{recommendation.get('impact_value'):,.0f}. "
            f"Effort: {recommendation.get('implementation_effort')}. "
            f"Is this viable? What risks exist?"
        )

        response = await self.claude_client.generate_response(
            query,
            context,
            ResponseStyle.TECHNICAL
        )

        return {
            "validation": response.generated_response,
            "risks": response.relevant_sections,
            "confidence": response.confidence_score
        }

    def get_conversation_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent conversation history"""

        history = self.conversation_history[-limit:]
        return [
            {
                "role": msg.role,
                "content": msg.content,
                "timestamp": msg.timestamp.isoformat()
            }
            for msg in history
        ]

    def clear_cache(self) -> None:
        """Clear response cache"""
        self.query_cache.clear()

    def get_stats(self) -> Dict[str, Any]:
        """Get integration statistics"""

        return {
            "total_queries": len(self.conversation_history) // 2,
            "cache_size": len(self.query_cache),
            "conversation_length": len(self.conversation_history),
            "cache_hit_rate": self._calculate_cache_hit_rate()
        }

    def _calculate_cache_hit_rate(self) -> float:
        """Calculate cache hit rate"""
        total_queries = len(self.conversation_history) // 2
        cached_responses = len(self.query_cache)

        if total_queries == 0:
            return 0.0

        return (cached_responses / total_queries) * 100


# Initialize global Claude integration
jarvis_claude = JarvisClaudeIntegration()
