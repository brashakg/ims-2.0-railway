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
from backend.core.jarvis_visualization_engine import (
    JarvisVisualizationEngine,
    ChartType,
    jarvis_visualizer
)
from backend.core.jarvis_voice_interface import (
    JarvisVoiceInterface,
    VoiceLanguage,
    VoiceGender,
    AudioFormat,
    jarvis_voice
)

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
# Visualization & Charts Endpoints
# ============================================================================

@router.post("/charts/sales-dashboard")
async def create_sales_dashboard(
    metrics: Dict[str, Any] = {},
    user_role: str = Query(...)
):
    """Create sales analytics dashboard"""

    if not await verify_superadmin_role(user_role):
        raise HTTPException(status_code=403, detail="SUPERADMIN role required")

    dashboard = jarvis_visualizer.create_sales_dashboard(metrics)

    return {
        "dashboard_id": dashboard.dashboard_id,
        "title": dashboard.title,
        "charts_count": len(dashboard.charts),
        "layout": dashboard.layout,
        "refresh_interval": dashboard.refresh_interval,
        "dashboard_data": json.loads(jarvis_visualizer.export_dashboard_json(dashboard))
    }


@router.post("/charts/inventory-dashboard")
async def create_inventory_dashboard(
    metrics: Dict[str, Any] = {},
    user_role: str = Query(...)
):
    """Create inventory management dashboard"""

    if not await verify_superadmin_role(user_role):
        raise HTTPException(status_code=403, detail="SUPERADMIN role required")

    dashboard = jarvis_visualizer.create_inventory_dashboard(metrics)

    return {
        "dashboard_id": dashboard.dashboard_id,
        "title": dashboard.title,
        "charts_count": len(dashboard.charts),
        "layout": dashboard.layout,
        "refresh_interval": dashboard.refresh_interval,
        "dashboard_data": json.loads(jarvis_visualizer.export_dashboard_json(dashboard))
    }


@router.post("/charts/compliance-dashboard")
async def create_compliance_dashboard(
    metrics: Dict[str, Any] = {},
    user_role: str = Query(...)
):
    """Create compliance monitoring dashboard"""

    if not await verify_superadmin_role(user_role):
        raise HTTPException(status_code=403, detail="SUPERADMIN role required")

    dashboard = jarvis_visualizer.create_compliance_dashboard(metrics)

    return {
        "dashboard_id": dashboard.dashboard_id,
        "title": dashboard.title,
        "charts_count": len(dashboard.charts),
        "layout": dashboard.layout,
        "refresh_interval": dashboard.refresh_interval,
        "dashboard_data": json.loads(jarvis_visualizer.export_dashboard_json(dashboard))
    }


@router.get("/charts/{chart_id}")
async def get_chart(
    chart_id: str,
    user_role: str = Query(...)
):
    """Get chart by ID"""

    if not await verify_superadmin_role(user_role):
        raise HTTPException(status_code=403, detail="SUPERADMIN role required")

    chart = jarvis_visualizer.get_chart_by_id(chart_id)

    if not chart:
        raise HTTPException(status_code=404, detail="Chart not found")

    return json.loads(jarvis_visualizer.export_chart_json(chart))


@router.get("/dashboards/{dashboard_id}")
async def get_dashboard(
    dashboard_id: str,
    user_role: str = Query(...)
):
    """Get dashboard by ID"""

    if not await verify_superadmin_role(user_role):
        raise HTTPException(status_code=403, detail="SUPERADMIN role required")

    dashboard = jarvis_visualizer.get_dashboard_by_id(dashboard_id)

    if not dashboard:
        raise HTTPException(status_code=404, detail="Dashboard not found")

    return json.loads(jarvis_visualizer.export_dashboard_json(dashboard))


@router.get("/dashboards")
async def list_dashboards(user_role: str = Query(...)):
    """List all dashboards"""

    if not await verify_superadmin_role(user_role):
        raise HTTPException(status_code=403, detail="SUPERADMIN role required")

    dashboards = jarvis_visualizer.list_dashboards()

    return {
        "dashboards": [
            {
                "dashboard_id": d.dashboard_id,
                "title": d.title,
                "description": d.description,
                "charts_count": len(d.charts),
                "created_at": d.created_at.isoformat()
            }
            for d in dashboards
        ],
        "count": len(dashboards)
    }


@router.get("/charts/recent")
async def get_recent_charts(
    limit: int = Query(10, ge=1, le=50),
    user_role: str = Query(...)
):
    """Get recently created charts"""

    if not await verify_superadmin_role(user_role):
        raise HTTPException(status_code=403, detail="SUPERADMIN role required")

    charts = jarvis_visualizer.list_recent_charts(limit)

    return {
        "charts": [
            json.loads(jarvis_visualizer.export_chart_json(chart))
            for chart in charts
        ],
        "count": len(charts)
    }


# ============================================================================
# Voice Interface Endpoints
# ============================================================================

@router.post("/voice/process-query")
async def process_voice_query(
    user_id: str = Query(...),
    user_role: str = Query(...),
    language: str = Query("en-US")
):
    """
    Process voice query end-to-end

    Accepts audio input, recognizes speech, generates response,
    and synthesizes audio output.

    Supported languages: en-US, en-GB, hi-IN, es-ES, fr-FR, de-DE, pt-BR, zh-CN, ja-JP
    """

    if not await verify_superadmin_role(user_role):
        raise HTTPException(status_code=403, detail="SUPERADMIN role required")

    try:
        # Map language string to enum
        language_map = {
            "en-US": VoiceLanguage.ENGLISH_US,
            "en-GB": VoiceLanguage.ENGLISH_UK,
            "hi-IN": VoiceLanguage.HINDI,
            "es-ES": VoiceLanguage.SPANISH,
            "fr-FR": VoiceLanguage.FRENCH,
            "de-DE": VoiceLanguage.GERMAN,
            "pt-BR": VoiceLanguage.PORTUGUESE,
            "zh-CN": VoiceLanguage.CHINESE_MANDARIN,
            "ja-JP": VoiceLanguage.JAPANESE
        }

        voice_language = language_map.get(language, VoiceLanguage.ENGLISH_US)

        # In a real implementation, receive audio data from request
        # For now, return mock audio data
        result = await jarvis_voice.process_voice_query(
            audio_data=b"mock_audio_data",
            user_id=user_id,
            language=voice_language
        )

        return {
            "recognized_query": result["recognized_query"],
            "recognition_confidence": result["recognition_confidence"],
            "response_text": result["response_text"],
            "response_duration_ms": result["response_duration_ms"],
            "alternatives": result["alternatives"],
            "session_id": result["session_id"],
            "language": language
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing voice query: {str(e)}")


@router.post("/voice/text-to-speech")
async def text_to_speech(
    text: str = Query(...),
    language: str = Query("en-US"),
    gender: str = Query("neutral"),
    speed: float = Query(1.0, ge=0.25, le=4.0),
    pitch: float = Query(1.0, ge=0.25, le=4.0),
    user_role: str = Query(...)
):
    """
    Convert text to speech

    Synthesizes natural language audio from text.

    Genders: male, female, neutral
    Speed: 0.25 - 4.0 (1.0 = normal)
    Pitch: 0.25 - 4.0 (1.0 = normal)
    """

    if not await verify_superadmin_role(user_role):
        raise HTTPException(status_code=403, detail="SUPERADMIN role required")

    try:
        # Map string values to enums
        language_map = {
            "en-US": VoiceLanguage.ENGLISH_US,
            "en-GB": VoiceLanguage.ENGLISH_UK,
            "hi-IN": VoiceLanguage.HINDI,
            "es-ES": VoiceLanguage.SPANISH,
            "fr-FR": VoiceLanguage.FRENCH,
            "de-DE": VoiceLanguage.GERMAN,
            "pt-BR": VoiceLanguage.PORTUGUESE,
            "zh-CN": VoiceLanguage.CHINESE_MANDARIN,
            "ja-JP": VoiceLanguage.JAPANESE
        }

        gender_map = {
            "male": VoiceGender.MALE,
            "female": VoiceGender.FEMALE,
            "neutral": VoiceGender.NEUTRAL
        }

        from backend.core.jarvis_voice_interface import TextToSpeechRequest

        tts_request = TextToSpeechRequest(
            text=text,
            language=language_map.get(language, VoiceLanguage.ENGLISH_US),
            gender=gender_map.get(gender, VoiceGender.NEUTRAL),
            speed=speed,
            pitch=pitch
        )

        response = await jarvis_voice.tts.synthesize_speech(tts_request)

        return {
            "duration_ms": response.duration_ms,
            "language": response.language.value,
            "gender": response.gender.value,
            "characters_processed": response.characters_processed,
            "audio_format": response.audio_format.value,
            "created_at": response.created_at.isoformat()
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error synthesizing speech: {str(e)}")


@router.get("/voice/profiles")
async def get_voice_profiles(
    language: Optional[str] = Query(None),
    gender: Optional[str] = Query(None),
    user_role: str = Query(...)
):
    """Get available voice profiles"""

    if not await verify_superadmin_role(user_role):
        raise HTTPException(status_code=403, detail="SUPERADMIN role required")

    language_enum = None
    if language:
        language_map = {
            "en-US": VoiceLanguage.ENGLISH_US,
            "en-GB": VoiceLanguage.ENGLISH_UK,
            "hi-IN": VoiceLanguage.HINDI
        }
        language_enum = language_map.get(language)

    gender_enum = None
    if gender:
        gender_map = {
            "male": VoiceGender.MALE,
            "female": VoiceGender.FEMALE,
            "neutral": VoiceGender.NEUTRAL
        }
        gender_enum = gender_map.get(gender)

    profiles = jarvis_voice.tts.get_voice_profiles(language_enum, gender_enum)

    return {
        "profiles": [
            {
                "language": p["language"].value,
                "gender": p["gender"].value,
                "tone": p["tone"],
                "speed_range": p["speed_range"]
            }
            for p in profiles
        ],
        "count": len(profiles)
    }


@router.get("/voice/sessions/{user_id}")
async def get_voice_sessions(
    user_id: str,
    user_role: str = Query(...)
):
    """Get voice sessions for user"""

    if not await verify_superadmin_role(user_role):
        raise HTTPException(status_code=403, detail="SUPERADMIN role required")

    sessions = jarvis_voice.get_voice_sessions(user_id)

    return {
        "sessions": [
            {
                "session_id": s.session_id,
                "language": s.language.value,
                "gender_preference": s.gender_preference.value,
                "created_at": s.created_at.isoformat(),
                "is_active": s.is_active,
                "total_queries": s.total_queries,
                "total_audio_duration_ms": s.total_audio_duration_ms
            }
            for s in sessions
        ],
        "count": len(sessions)
    }


@router.post("/voice/sessions/{session_id}/end")
async def end_voice_session(
    session_id: str,
    user_role: str = Query(...)
):
    """End a voice session"""

    if not await verify_superadmin_role(user_role):
        raise HTTPException(status_code=403, detail="SUPERADMIN role required")

    success = jarvis_voice.end_voice_session(session_id)

    if not success:
        raise HTTPException(status_code=404, detail="Session not found")

    return {
        "success": True,
        "session_id": session_id,
        "ended_at": datetime.now().isoformat()
    }


@router.get("/voice/history/{user_id}")
async def get_voice_history(
    user_id: str,
    limit: int = Query(20, ge=1, le=100),
    user_role: str = Query(...)
):
    """Get voice interaction history for user"""

    if not await verify_superadmin_role(user_role):
        raise HTTPException(status_code=403, detail="SUPERADMIN role required")

    history = jarvis_voice.get_interaction_history(user_id, limit)

    return {
        "interactions": history,
        "count": len(history)
    }


@router.get("/voice/stats")
async def get_voice_stats(user_role: str = Query(...)):
    """Get voice system statistics"""

    if not await verify_superadmin_role(user_role):
        raise HTTPException(status_code=403, detail="SUPERADMIN role required")

    stats = jarvis_voice.get_system_stats()

    return stats


@router.get("/voice/languages")
async def get_supported_languages(user_role: str = Query(...)):
    """Get supported languages for voice"""

    if not await verify_superadmin_role(user_role):
        raise HTTPException(status_code=403, detail="SUPERADMIN role required")

    languages = [
        {
            "code": lang.value,
            "name": lang.name.replace("_", " ")
        }
        for lang in VoiceLanguage
    ]

    return {
        "languages": languages,
        "count": len(languages)
    }


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
