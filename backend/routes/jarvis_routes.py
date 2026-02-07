"""
IMS 2.0 - JARVIS API Routes
===========================

FastAPI routes for JARVIS AI system.
Handles query processing, real-time updates, recommendations, and system management.
SUPERADMIN only endpoints.
"""

from fastapi import APIRouter, HTTPException, Query, Depends, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime
import json
import asyncio

# Import JARVIS modules
from backend.core.jarvis_ai_orchestrator import (
    JarvisAIOrchestrator,
    ExecutionMode,
    jarvis_orchestrator
)
from backend.core.jarvis_claude_integration import ResponseStyle

# Import authentication/authorization
# (Assuming these exist in your backend)
# from backend.auth import get_current_user, require_role

# Create router
router = APIRouter(prefix="/api/jarvis", tags=["JARVIS AI"])


# ============================================================================
# Request/Response Models
# ============================================================================

class JarvisQueryRequest(BaseModel):
    """Request model for JARVIS query"""
    query: str
    mode: str = "interactive"  # interactive, batch, streaming
    include_analytics: bool = True
    include_recommendations: bool = True
    include_compliance: bool = False
    response_style: str = "detailed"  # concise, detailed, executive, technical
    store_context: Optional[str] = None


class JarvisQueryResponse(BaseModel):
    """Response from JARVIS query"""
    query_id: str
    original_query: str
    primary_response: str
    analytics_insights: Optional[Dict[str, Any]]
    recommendations: Optional[List[Dict[str, Any]]]
    active_alerts: List[Dict[str, Any]]
    compliance_status: Optional[Dict[str, Any]]
    anomalies_detected: List[Dict[str, Any]]
    suggested_actions: List[str]
    confidence_score: float
    execution_time_ms: int


class ConversationHistoryResponse(BaseModel):
    """Conversation history response"""
    role: str
    content: str
    timestamp: str


class DashboardSummaryResponse(BaseModel):
    """Dashboard summary response"""
    dashboard_id: str
    generated_at: str
    alerts: Dict[str, Any]
    recommendations: List[Dict[str, Any]]
    compliance: Dict[str, Any]
    query_history: List[str]


class SystemStatsResponse(BaseModel):
    """System statistics response"""
    total_queries_processed: int
    cache_size: int
    alerts_in_system: int
    compliance_violations: int
    active_streaming_sessions: int
    connected_clients: int
    cache_hit_rate: float


# ============================================================================
# Authentication Helper
# ============================================================================

async def verify_superadmin_role(user_role: str) -> bool:
    """Verify user has SUPERADMIN role"""
    return user_role == "SUPERADMIN"


# ============================================================================
# Query Endpoints
# ============================================================================

@router.post("/query", response_model=JarvisQueryResponse)
async def process_query(
    request: JarvisQueryRequest,
    user_id: str = Query(...),
    user_role: str = Query(...)
):
    """
    Process natural language query through JARVIS AI

    Requires SUPERADMIN role.

    Query modes:
    - interactive: Real-time response (default)
    - batch: Deferred processing
    - streaming: Real-time token streaming

    Response styles:
    - concise: Brief answers
    - detailed: Comprehensive analysis
    - executive: High-level summary
    - technical: In-depth details
    """

    # Verify SUPERADMIN role
    if not await verify_superadmin_role(user_role):
        raise HTTPException(status_code=403, detail="SUPERADMIN role required")

    try:
        # Map string values to enums
        mode_map = {
            "interactive": ExecutionMode.INTERACTIVE,
            "batch": ExecutionMode.BATCH,
            "streaming": ExecutionMode.STREAMING,
            "scheduled": ExecutionMode.SCHEDULED
        }

        mode = mode_map.get(request.mode, ExecutionMode.INTERACTIVE)

        style_map = {
            "concise": ResponseStyle.CONCISE,
            "detailed": ResponseStyle.DETAILED,
            "executive": ResponseStyle.EXECUTIVE,
            "technical": ResponseStyle.TECHNICAL,
            "actionable": ResponseStyle.ACTIONABLE
        }

        style = style_map.get(request.response_style, ResponseStyle.DETAILED)

        # Process query
        response = await jarvis_orchestrator.process_query(
            user_query=request.query,
            user_id=user_id,
            user_role=user_role,
            mode=mode,
            store_context=request.store_context
        )

        return response

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing query: {str(e)}")


@router.get("/query/{query_id}")
async def get_query_result(
    query_id: str,
    user_role: str = Query(...)
):
    """Retrieve cached query result"""

    if not await verify_superadmin_role(user_role):
        raise HTTPException(status_code=403, detail="SUPERADMIN role required")

    if query_id not in jarvis_orchestrator.response_cache:
        raise HTTPException(status_code=404, detail="Query not found")

    response = jarvis_orchestrator.response_cache[query_id]

    return {
        "query_id": response.query_id,
        "original_query": response.original_query,
        "primary_response": response.primary_response,
        "analytics_insights": response.analytics_insights,
        "recommendations": response.recommendations,
        "active_alerts": response.active_alerts,
        "compliance_status": response.compliance_status,
        "anomalies_detected": response.anomalies_detected,
        "suggested_actions": response.suggested_actions,
        "confidence_score": response.confidence_score,
        "execution_time_ms": response.execution_time_ms
    }


@router.get("/conversation-history")
async def get_conversation_history(
    limit: int = Query(20, ge=1, le=100),
    user_role: str = Query(...)
):
    """Get conversation history with JARVIS"""

    if not await verify_superadmin_role(user_role):
        raise HTTPException(status_code=403, detail="SUPERADMIN role required")

    history = jarvis_orchestrator.claude.get_conversation_history(limit)

    return {
        "history": history,
        "count": len(history)
    }


# ============================================================================
# Real-time Endpoints
# ============================================================================

@router.websocket("/ws/stream/{client_id}")
async def websocket_streaming(
    websocket: WebSocket,
    client_id: str,
    user_id: str,
    user_role: str
):
    """
    WebSocket endpoint for real-time streaming responses and alerts

    Requires SUPERADMIN role.
    """

    # Verify role
    if user_role != "SUPERADMIN":
        await websocket.close(code=4003, reason="SUPERADMIN role required")
        return

    await websocket.accept()

    # Register connection
    connection = await jarvis_orchestrator.realtime.handle_new_connection(
        client_id,
        user_id,
        user_role
    )

    try:
        while True:
            # Receive message from client
            data = await websocket.receive_text()
            message = json.loads(data)

            msg_type = message.get("type")

            # Handle subscribe to alerts
            if msg_type == "subscribe_alerts":
                alert_types = message.get("alert_types", [])
                await jarvis_orchestrator.realtime.subscribe_to_alerts(
                    user_id,
                    alert_types
                )
                await websocket.send_text(json.dumps({
                    "type": "subscription_confirmed",
                    "alert_types": alert_types
                }))

            # Handle subscribe to metrics
            elif msg_type == "subscribe_metrics":
                metrics = message.get("metrics", [])
                await jarvis_orchestrator.realtime.subscribe_to_metrics(
                    user_id,
                    metrics
                )
                await websocket.send_text(json.dumps({
                    "type": "subscription_confirmed",
                    "metrics": metrics
                }))

            # Handle message acknowledgement
            elif msg_type == "ack":
                message_id = message.get("message_id")
                await jarvis_orchestrator.realtime.acknowledge_message(
                    client_id,
                    message_id
                )

            # Handle heartbeat
            elif msg_type == "heartbeat":
                await jarvis_orchestrator.realtime.send_heartbeat(client_id)

            # Check for pending messages and send
            pending = await jarvis_orchestrator.realtime.get_pending_messages(client_id)
            if pending:
                for msg in pending:
                    await websocket.send_text(json.dumps(msg))

            # Small delay
            await asyncio.sleep(0.1)

    except WebSocketDisconnect:
        await jarvis_orchestrator.realtime.handle_disconnection(client_id)
    except Exception as e:
        print(f"WebSocket error: {str(e)}")
        await jarvis_orchestrator.realtime.handle_disconnection(client_id)


@router.post("/stream/{stream_id}/chunk")
async def send_stream_chunk(
    stream_id: str,
    chunk: str = Query(...),
    is_final: bool = Query(False),
    user_role: str = Query(...)
):
    """Send streaming response chunk"""

    if not await verify_superadmin_role(user_role):
        raise HTTPException(status_code=403, detail="SUPERADMIN role required")

    success = await jarvis_orchestrator.realtime.stream_chunk(
        stream_id,
        chunk,
        is_final
    )

    return {
        "success": success,
        "stream_id": stream_id,
        "is_final": is_final
    }


# ============================================================================
# Dashboard & Summary Endpoints
# ============================================================================

@router.get("/dashboard", response_model=DashboardSummaryResponse)
async def get_dashboard_summary(
    user_id: str = Query(...),
    user_role: str = Query(...)
):
    """Get JARVIS dashboard summary"""

    if not await verify_superadmin_role(user_role):
        raise HTTPException(status_code=403, detail="SUPERADMIN role required")

    summary = await jarvis_orchestrator.get_dashboard_summary(user_id)

    return summary


@router.get("/stats", response_model=SystemStatsResponse)
async def get_system_stats(user_role: str = Query(...)):
    """Get JARVIS system statistics"""

    if not await verify_superadmin_role(user_role):
        raise HTTPException(status_code=403, detail="SUPERADMIN role required")

    stats = jarvis_orchestrator.get_system_stats()

    return stats


# ============================================================================
# Alert Management Endpoints
# ============================================================================

@router.get("/alerts")
async def get_active_alerts(user_role: str = Query(...)):
    """Get active alerts"""

    if not await verify_superadmin_role(user_role):
        raise HTTPException(status_code=403, detail="SUPERADMIN role required")

    alerts = jarvis_orchestrator.alerts.get_active_alerts()

    return {
        "alerts": [
            {
                "id": a.id,
                "title": a.title,
                "severity": a.severity.value,
                "status": a.status.value,
                "created_at": a.created_at.isoformat(),
                "description": a.description
            }
            for a in alerts
        ],
        "count": len(alerts)
    }


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(
    alert_id: str,
    user_id: str = Query(...),
    user_role: str = Query(...)
):
    """Acknowledge an alert"""

    if not await verify_superadmin_role(user_role):
        raise HTTPException(status_code=403, detail="SUPERADMIN role required")

    alert = jarvis_orchestrator.alerts.acknowledge_alert(alert_id, user_id)

    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    return {
        "success": True,
        "alert_id": alert_id,
        "acknowledged_by": user_id,
        "acknowledged_at": alert.acknowledged_at.isoformat()
    }


@router.post("/alerts/{alert_id}/resolve")
async def resolve_alert(
    alert_id: str,
    user_id: str = Query(...),
    resolution_notes: str = Query(""),
    user_role: str = Query(...)
):
    """Resolve an alert"""

    if not await verify_superadmin_role(user_role):
        raise HTTPException(status_code=403, detail="SUPERADMIN role required")

    alert = jarvis_orchestrator.alerts.resolve_alert(alert_id, user_id, resolution_notes)

    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    return {
        "success": True,
        "alert_id": alert_id,
        "resolved_by": user_id,
        "resolved_at": alert.resolved_at.isoformat()
    }


@router.post("/alerts/{alert_id}/escalate")
async def escalate_alert(
    alert_id: str,
    user_role: str = Query(...)
):
    """Escalate an alert"""

    if not await verify_superadmin_role(user_role):
        raise HTTPException(status_code=403, detail="SUPERADMIN role required")

    alert = asyncio.run(jarvis_orchestrator.alerts.escalate_alert(alert_id))

    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found or max escalation reached")

    return {
        "success": True,
        "alert_id": alert_id,
        "escalation_level": alert.escalation_level
    }


# ============================================================================
# Recommendations Endpoints
# ============================================================================

@router.get("/recommendations")
async def get_recommendations(
    category: Optional[str] = Query(None),
    user_role: str = Query(...)
):
    """Get active recommendations"""

    if not await verify_superadmin_role(user_role):
        raise HTTPException(status_code=403, detail="SUPERADMIN role required")

    recommendations = jarvis_orchestrator.recommender.recommendations

    if category:
        recommendations = [
            r for r in recommendations
            if r.category.value == category
        ]

    return {
        "recommendations": [
            {
                "id": r.id,
                "title": r.title,
                "category": r.category.value,
                "priority": r.priority.value,
                "impact_value": r.impact_value,
                "confidence_score": r.confidence_score,
                "status": r.status
            }
            for r in recommendations
        ],
        "count": len(recommendations)
    }


# ============================================================================
# Compliance Endpoints
# ============================================================================

@router.get("/compliance/status")
async def get_compliance_status(user_role: str = Query(...)):
    """Get compliance status"""

    if not await verify_superadmin_role(user_role):
        raise HTTPException(status_code=403, detail="SUPERADMIN role required")

    violations = jarvis_orchestrator.compliance.violations
    compliance_score = jarvis_orchestrator.compliance.calculate_compliance_score(violations)

    return {
        "compliance_score": compliance_score,
        "total_violations": len(violations),
        "critical_violations": sum(
            1 for v in violations if v.severity == "critical"
        ),
        "violations": [
            {
                "id": v.id,
                "rule_id": v.rule_id,
                "severity": v.severity,
                "status": v.status,
                "created_at": v.created_at.isoformat()
            }
            for v in violations[:10]  # Last 10
        ]
    }


# ============================================================================
# System Management Endpoints
# ============================================================================

@router.delete("/cache")
async def clear_cache(user_role: str = Query(...)):
    """Clear response cache"""

    if not await verify_superadmin_role(user_role):
        raise HTTPException(status_code=403, detail="SUPERADMIN role required")

    cache_size_before = len(jarvis_orchestrator.response_cache)
    jarvis_orchestrator.claude.clear_cache()
    jarvis_orchestrator.response_cache.clear()
    cache_size_after = len(jarvis_orchestrator.response_cache)

    return {
        "success": True,
        "cache_cleared": cache_size_before - cache_size_after,
        "new_cache_size": cache_size_after
    }


@router.get("/realtime-stats")
async def get_realtime_stats(user_role: str = Query(...)):
    """Get real-time connection statistics"""

    if not await verify_superadmin_role(user_role):
        raise HTTPException(status_code=403, detail="SUPERADMIN role required")

    stats = await jarvis_orchestrator.realtime.get_connection_stats()

    return stats


# ============================================================================
# Health Check
# ============================================================================

@router.get("/health")
async def health_check():
    """Health check endpoint"""

    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "1.0.0"
    }
