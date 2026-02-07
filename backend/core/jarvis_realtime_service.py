"""
IMS 2.0 - JARVIS Real-time Service
==================================

WebSocket-based real-time communication layer for JARVIS AI.
Handles streaming responses, live alerts, metrics updates, and
bidirectional communication with frontend.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional, Callable, Set
from enum import Enum
from datetime import datetime, timedelta
import json
import asyncio
from abc import ABC, abstractmethod


class MessageType(Enum):
    """Types of real-time messages"""
    QUERY_RESPONSE = "query_response"
    ALERT_NOTIFICATION = "alert_notification"
    METRIC_UPDATE = "metric_update"
    RECOMMENDATION_UPDATE = "recommendation_update"
    COMPLIANCE_UPDATE = "compliance_update"
    SYSTEM_STATUS = "system_status"
    ERROR = "error"
    HEARTBEAT = "heartbeat"
    STREAMING_START = "streaming_start"
    STREAMING_CHUNK = "streaming_chunk"
    STREAMING_END = "streaming_end"


class MessagePriority(Enum):
    """Message priority levels for delivery"""
    LOW = 1
    NORMAL = 2
    HIGH = 3
    CRITICAL = 4


@dataclass
class RealtimeMessage:
    """Message for real-time communication"""
    id: str
    type: MessageType
    priority: MessagePriority
    payload: Dict[str, Any]
    timestamp: datetime = field(default_factory=datetime.now)
    user_id: Optional[str] = None
    source: str = "jarvis"
    requires_ack: bool = False
    retry_count: int = 0
    max_retries: int = 3


@dataclass
class StreamingResponse:
    """Streaming response session"""
    stream_id: str
    query_id: str
    user_id: str
    total_chunks: int = 0
    received_chunks: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    completed: bool = False
    error: Optional[str] = None


@dataclass
class ClientConnection:
    """Active WebSocket client connection"""
    client_id: str
    user_id: str
    role: str
    connected_at: datetime = field(default_factory=datetime.now)
    last_heartbeat: datetime = field(default_factory=datetime.now)
    subscribed_alerts: Set[str] = field(default_factory=set)
    subscribed_metrics: Set[str] = field(default_factory=set)
    message_queue: List[RealtimeMessage] = field(default_factory=list)
    is_active: bool = True


class ConnectionManager(ABC):
    """Abstract base for connection management"""

    @abstractmethod
    async def connect(self, client_id: str, user_id: str, role: str) -> ClientConnection:
        """Register new WebSocket connection"""
        pass

    @abstractmethod
    async def disconnect(self, client_id: str) -> None:
        """Remove WebSocket connection"""
        pass

    @abstractmethod
    async def send_message(self, client_id: str, message: RealtimeMessage) -> bool:
        """Send message to specific client"""
        pass

    @abstractmethod
    async def broadcast_message(self, message: RealtimeMessage, user_role: Optional[str] = None) -> int:
        """Broadcast message to all/filtered clients"""
        pass


class MockConnectionManager(ConnectionManager):
    """Mock connection manager for testing"""

    def __init__(self):
        self.clients: Dict[str, ClientConnection] = {}
        self.message_history: List[RealtimeMessage] = []
        self.max_history = 1000

    async def connect(self, client_id: str, user_id: str, role: str) -> ClientConnection:
        """Register new connection"""
        connection = ClientConnection(
            client_id=client_id,
            user_id=user_id,
            role=role
        )
        self.clients[client_id] = connection
        print(f"Client connected: {client_id} ({user_id})")
        return connection

    async def disconnect(self, client_id: str) -> None:
        """Remove connection"""
        if client_id in self.clients:
            del self.clients[client_id]
            print(f"Client disconnected: {client_id}")

    async def send_message(self, client_id: str, message: RealtimeMessage) -> bool:
        """Send message to client"""
        if client_id not in self.clients:
            return False

        client = self.clients[client_id]
        client.message_queue.append(message)

        # Store in history
        self.message_history.append(message)
        if len(self.message_history) > self.max_history:
            self.message_history = self.message_history[-self.max_history:]

        return True

    async def broadcast_message(
        self,
        message: RealtimeMessage,
        user_role: Optional[str] = None
    ) -> int:
        """Broadcast message to clients"""
        count = 0

        for client in self.clients.values():
            # Filter by role if specified
            if user_role and client.role != user_role:
                continue

            # Check subscriptions for metric/alert messages
            if message.type == MessageType.METRIC_UPDATE:
                metric_name = message.payload.get("metric_name", "")
                if metric_name not in client.subscribed_metrics:
                    continue

            elif message.type == MessageType.ALERT_NOTIFICATION:
                alert_type = message.payload.get("alert_type", "")
                if alert_type not in client.subscribed_alerts:
                    continue

            client.message_queue.append(message)
            count += 1

        # Store in history
        self.message_history.append(message)
        if len(self.message_history) > self.max_history:
            self.message_history = self.message_history[-self.max_history:]

        return count


class JarvisRealtimeService:
    """Real-time service for JARVIS AI"""

    def __init__(self, connection_manager: Optional[ConnectionManager] = None):
        self.connection_manager = connection_manager or MockConnectionManager()
        self.streaming_sessions: Dict[str, StreamingResponse] = {}
        self.metric_updates: Dict[str, Dict[str, Any]] = {}
        self.alert_subscriptions: Dict[str, Set[str]] = {}  # user_id -> alert_types
        self.metric_subscriptions: Dict[str, Set[str]] = {}  # user_id -> metrics

    async def handle_new_connection(
        self,
        client_id: str,
        user_id: str,
        role: str
    ) -> ClientConnection:
        """Handle new WebSocket connection"""

        connection = await self.connection_manager.connect(client_id, user_id, role)

        # Send welcome message
        welcome = RealtimeMessage(
            id=f"welcome_{client_id}",
            type=MessageType.SYSTEM_STATUS,
            priority=MessagePriority.NORMAL,
            payload={
                "status": "connected",
                "message": f"Welcome to JARVIS AI, {role}",
                "server_time": datetime.now().isoformat()
            },
            user_id=user_id
        )

        await self.connection_manager.send_message(client_id, welcome)

        return connection

    async def handle_disconnection(self, client_id: str) -> None:
        """Handle WebSocket disconnection"""
        await self.connection_manager.disconnect(client_id)

    async def start_streaming_response(
        self,
        query_id: str,
        user_id: str,
        query_text: str
    ) -> StreamingResponse:
        """Start streaming response session"""

        stream_id = f"stream_{int(datetime.now().timestamp())}"
        session = StreamingResponse(
            stream_id=stream_id,
            query_id=query_id,
            user_id=user_id
        )

        self.streaming_sessions[stream_id] = session

        # Notify user that streaming is starting
        start_msg = RealtimeMessage(
            id=f"stream_start_{stream_id}",
            type=MessageType.STREAMING_START,
            priority=MessagePriority.NORMAL,
            payload={
                "stream_id": stream_id,
                "query_id": query_id,
                "query": query_text
            },
            user_id=user_id
        )

        # Find client and send message
        for client_id, client in self.connection_manager.clients.items():
            if client.user_id == user_id:
                await self.connection_manager.send_message(client_id, start_msg)

        return session

    async def stream_chunk(
        self,
        stream_id: str,
        chunk: str,
        is_final: bool = False
    ) -> bool:
        """Send streaming response chunk"""

        if stream_id not in self.streaming_sessions:
            return False

        session = self.streaming_sessions[stream_id]
        session.total_chunks += 1

        # Create chunk message
        chunk_msg = RealtimeMessage(
            id=f"chunk_{stream_id}_{session.total_chunks}",
            type=MessageType.STREAMING_CHUNK,
            priority=MessagePriority.NORMAL,
            payload={
                "stream_id": stream_id,
                "chunk_number": session.total_chunks,
                "content": chunk,
                "is_final": is_final
            },
            user_id=session.user_id
        )

        # Find and send to user's clients
        client_count = 0
        for client_id, client in self.connection_manager.clients.items():
            if client.user_id == session.user_id:
                await self.connection_manager.send_message(client_id, chunk_msg)
                client_count += 1

        if is_final:
            session.completed = True
            await self._cleanup_stream(stream_id)

        return client_count > 0

    async def send_alert(
        self,
        alert_data: Dict[str, Any],
        target_role: str = "SUPERADMIN",
        priority: MessagePriority = MessagePriority.HIGH
    ) -> int:
        """Send alert to relevant users"""

        alert_msg = RealtimeMessage(
            id=f"alert_{int(datetime.now().timestamp())}",
            type=MessageType.ALERT_NOTIFICATION,
            priority=priority,
            payload={
                "alert_id": alert_data.get("id"),
                "title": alert_data.get("title"),
                "description": alert_data.get("description"),
                "severity": alert_data.get("severity"),
                "category": alert_data.get("category"),
                "source": alert_data.get("source"),
                "timestamp": alert_data.get("triggered_at", datetime.now().isoformat())
            },
            requires_ack=True
        )

        # Broadcast to specific role
        broadcast_count = await self.connection_manager.broadcast_message(
            alert_msg,
            user_role=target_role
        )

        return broadcast_count

    async def send_metric_update(
        self,
        metric_name: str,
        metric_value: float,
        metric_data: Dict[str, Any],
        priority: MessagePriority = MessagePriority.NORMAL
    ) -> int:
        """Send metric update to subscribed users"""

        metric_msg = RealtimeMessage(
            id=f"metric_{metric_name}_{int(datetime.now().timestamp())}",
            type=MessageType.METRIC_UPDATE,
            priority=priority,
            payload={
                "metric_name": metric_name,
                "value": metric_value,
                "data": metric_data,
                "timestamp": datetime.now().isoformat()
            }
        )

        # Store latest metric
        self.metric_updates[metric_name] = {
            "value": metric_value,
            "data": metric_data,
            "timestamp": datetime.now()
        }

        # Broadcast to subscribed users
        broadcast_count = await self.connection_manager.broadcast_message(metric_msg)

        return broadcast_count

    async def send_recommendation_update(
        self,
        recommendation_data: Dict[str, Any],
        priority: MessagePriority = MessagePriority.NORMAL
    ) -> int:
        """Send recommendation update"""

        rec_msg = RealtimeMessage(
            id=f"rec_{recommendation_data.get('id')}",
            type=MessageType.RECOMMENDATION_UPDATE,
            priority=priority,
            payload={
                "recommendation_id": recommendation_data.get("id"),
                "title": recommendation_data.get("title"),
                "priority": recommendation_data.get("priority"),
                "impact_value": recommendation_data.get("impact_value"),
                "status": recommendation_data.get("status")
            },
            requires_ack=True
        )

        # Broadcast only to SUPERADMIN
        broadcast_count = await self.connection_manager.broadcast_message(
            rec_msg,
            user_role="SUPERADMIN"
        )

        return broadcast_count

    async def send_compliance_update(
        self,
        compliance_data: Dict[str, Any],
        priority: MessagePriority = MessagePriority.HIGH
    ) -> int:
        """Send compliance update"""

        comp_msg = RealtimeMessage(
            id=f"compliance_{int(datetime.now().timestamp())}",
            type=MessageType.COMPLIANCE_UPDATE,
            priority=priority,
            payload={
                "compliance_score": compliance_data.get("compliance_score"),
                "violations_count": compliance_data.get("violations_count"),
                "critical_violations": compliance_data.get("critical_violations", []),
                "status": compliance_data.get("status")
            },
            requires_ack=True
        )

        # Broadcast only to SUPERADMIN
        broadcast_count = await self.connection_manager.broadcast_message(
            comp_msg,
            user_role="SUPERADMIN"
        )

        return broadcast_count

    async def subscribe_to_alerts(
        self,
        user_id: str,
        alert_types: List[str]
    ) -> bool:
        """Subscribe user to alert types"""

        if user_id not in self.alert_subscriptions:
            self.alert_subscriptions[user_id] = set()

        self.alert_subscriptions[user_id].update(alert_types)

        # Update connected clients
        for client_id, client in self.connection_manager.clients.items():
            if client.user_id == user_id:
                client.subscribed_alerts.update(alert_types)

        return True

    async def subscribe_to_metrics(
        self,
        user_id: str,
        metrics: List[str]
    ) -> bool:
        """Subscribe user to metric updates"""

        if user_id not in self.metric_subscriptions:
            self.metric_subscriptions[user_id] = set()

        self.metric_subscriptions[user_id].update(metrics)

        # Update connected clients
        for client_id, client in self.connection_manager.clients.items():
            if client.user_id == user_id:
                client.subscribed_metrics.update(metrics)

        return True

    async def get_pending_messages(self, client_id: str) -> List[Dict[str, Any]]:
        """Get pending messages for client"""

        if client_id not in self.connection_manager.clients:
            return []

        client = self.connection_manager.clients[client_id]
        messages = client.message_queue.copy()
        client.message_queue.clear()

        return [
            {
                "id": msg.id,
                "type": msg.type.value,
                "priority": msg.priority.value,
                "payload": msg.payload,
                "timestamp": msg.timestamp.isoformat(),
                "requires_ack": msg.requires_ack
            }
            for msg in messages
        ]

    async def acknowledge_message(self, client_id: str, message_id: str) -> bool:
        """Acknowledge receipt of message"""
        # In real implementation, would update delivery status
        return True

    async def send_heartbeat(self, client_id: str) -> bool:
        """Send heartbeat to keep connection alive"""

        if client_id not in self.connection_manager.clients:
            return False

        client = self.connection_manager.clients[client_id]
        client.last_heartbeat = datetime.now()

        heartbeat = RealtimeMessage(
            id=f"hb_{client_id}",
            type=MessageType.HEARTBEAT,
            priority=MessagePriority.LOW,
            payload={
                "client_id": client_id,
                "server_time": datetime.now().isoformat(),
                "message_queue_size": len(client.message_queue)
            }
        )

        await self.connection_manager.send_message(client_id, heartbeat)

        return True

    async def get_connection_stats(self) -> Dict[str, Any]:
        """Get real-time connection statistics"""

        active_connections = sum(
            1 for client in self.connection_manager.clients.values()
            if client.is_active
        )

        return {
            "total_connections": len(self.connection_manager.clients),
            "active_connections": active_connections,
            "streaming_sessions": len(self.streaming_sessions),
            "active_streams": sum(
                1 for s in self.streaming_sessions.values()
                if not s.completed
            ),
            "alert_subscriptions": len(self.alert_subscriptions),
            "metric_subscriptions": len(self.metric_subscriptions)
        }

    async def _cleanup_stream(self, stream_id: str) -> None:
        """Cleanup completed stream session"""
        # Keep for a few minutes before removing
        await asyncio.sleep(300)  # 5 minutes
        if stream_id in self.streaming_sessions:
            del self.streaming_sessions[stream_id]


# Initialize global real-time service
jarvis_realtime = JarvisRealtimeService()
