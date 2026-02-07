"""
IMS 2.0 - Real-time Alert System for JARVIS
============================================

Manages real-time alerts and notifications for critical business events.
Supports multiple channels: In-app, Email, SMS, WebSocket.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Callable
from enum import Enum
import json
from abc import ABC, abstractmethod


class AlertSeverity(Enum):
    """Alert severity levels"""
    INFO = 1
    WARNING = 2
    CRITICAL = 3
    EMERGENCY = 4


class AlertChannel(Enum):
    """Alert delivery channels"""
    IN_APP = "in_app"
    EMAIL = "email"
    SMS = "sms"
    WEBSOCKET = "websocket"
    SLACK = "slack"
    PAGERDUTY = "pagerduty"


class AlertStatus(Enum):
    """Alert status"""
    TRIGGERED = "triggered"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    ESCALATED = "escalated"


@dataclass
class Alert:
    """Real-time alert object"""
    id: str
    title: str
    description: str
    severity: AlertSeverity
    category: str
    source: str
    status: AlertStatus
    created_at: datetime
    triggered_at: datetime
    acknowledged_at: Optional[datetime] = None
    acknowledged_by: Optional[str] = None
    resolved_at: Optional[datetime] = None
    resolved_by: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)
    channels: List[AlertChannel] = field(default_factory=list)
    escalation_level: int = 0
    max_escalation: int = 3
    retry_count: int = 0
    last_retry: Optional[datetime] = None


@dataclass
class AlertRule:
    """Rule for triggering alerts"""
    id: str
    name: str
    description: str
    trigger_condition: str  # Description of condition
    severity: AlertSeverity
    channels: List[AlertChannel]
    enabled: bool = True
    notify_on_recovery: bool = True
    cooldown_minutes: int = 5  # Don't alert again for X minutes


@dataclass
class AlertTemplate:
    """Template for formatting alerts"""
    id: str
    name: str
    title_template: str
    message_template: str
    category: str
    default_channels: List[AlertChannel]


class AlertHandler(ABC):
    """Abstract base for alert handlers"""

    @abstractmethod
    async def send(self, alert: Alert) -> bool:
        """Send alert via specific channel"""
        pass

    @abstractmethod
    async def acknowledge(self, alert_id: str) -> bool:
        """Acknowledge alert"""
        pass


class InAppAlertHandler(AlertHandler):
    """In-app notification handler"""

    async def send(self, alert: Alert) -> bool:
        """Send in-app notification"""
        # Store in database and broadcast via WebSocket
        print(f"InApp Alert: {alert.title}")
        return True

    async def acknowledge(self, alert_id: str) -> bool:
        """Acknowledge in-app alert"""
        print(f"Acknowledged alert: {alert_id}")
        return True


class EmailAlertHandler(AlertHandler):
    """Email notification handler"""

    async def send(self, alert: Alert) -> bool:
        """Send email alert"""
        # Would integrate with email service
        print(f"Email Alert: {alert.title}")
        return True

    async def acknowledge(self, alert_id: str) -> bool:
        """Acknowledge email alert"""
        return True


class SMSAlertHandler(AlertHandler):
    """SMS notification handler"""

    async def send(self, alert: Alert) -> bool:
        """Send SMS alert"""
        # Would integrate with SMS service
        print(f"SMS Alert: {alert.title}")
        return True

    async def acknowledge(self, alert_id: str) -> bool:
        """Acknowledge SMS alert"""
        return True


class WebSocketAlertHandler(AlertHandler):
    """WebSocket real-time notification handler"""

    async def send(self, alert: Alert) -> bool:
        """Send via WebSocket for real-time updates"""
        print(f"WebSocket Alert: {alert.title}")
        # Would broadcast to connected clients
        return True

    async def acknowledge(self, alert_id: str) -> bool:
        """Acknowledge WebSocket alert"""
        return True


class JarvisAlertSystem:
    """Real-time alert management system"""

    def __init__(self):
        self.alerts: List[Alert] = []
        self.alert_rules: List[AlertRule] = []
        self.alert_templates: Dict[str, AlertTemplate] = {}
        self.handlers: Dict[AlertChannel, AlertHandler] = {
            AlertChannel.IN_APP: InAppAlertHandler(),
            AlertChannel.EMAIL: EmailAlertHandler(),
            AlertChannel.SMS: SMSAlertHandler(),
            AlertChannel.WEBSOCKET: WebSocketAlertHandler(),
        }
        self.alert_history: List[Alert] = []
        self.last_alert_time: Dict[str, datetime] = {}
        self._initialize_default_rules()
        self._initialize_templates()

    def _initialize_default_rules(self):
        """Initialize default alert rules"""
        self.alert_rules = [
            AlertRule(
                id="rule_critical_stock",
                name="Critical Stock Level",
                description="Alert when product stock reaches critical level",
                trigger_condition="stock_level < critical_threshold",
                severity=AlertSeverity.CRITICAL,
                channels=[AlertChannel.IN_APP, AlertChannel.EMAIL, AlertChannel.WEBSOCKET],
                cooldown_minutes=60,
            ),
            AlertRule(
                id="rule_system_down",
                name="System Down",
                description="Alert when critical system is unavailable",
                trigger_condition="system_availability < 90%",
                severity=AlertSeverity.EMERGENCY,
                channels=[AlertChannel.IN_APP, AlertChannel.SMS, AlertChannel.EMAIL, AlertChannel.WEBSOCKET],
                cooldown_minutes=5,
            ),
            AlertRule(
                id="rule_sales_anomaly",
                name="Sales Anomaly",
                description="Alert on unusual sales patterns",
                trigger_condition="sales_deviation > 2_sigma",
                severity=AlertSeverity.WARNING,
                channels=[AlertChannel.IN_APP, AlertChannel.WEBSOCKET],
                cooldown_minutes=30,
            ),
            AlertRule(
                id="rule_cash_discrepancy",
                name="Cash Discrepancy",
                description="Alert on cash count mismatch",
                trigger_condition="cash_variance > 0",
                severity=AlertSeverity.CRITICAL,
                channels=[AlertChannel.IN_APP, AlertChannel.EMAIL, AlertChannel.WEBSOCKET],
                cooldown_minutes=15,
            ),
            AlertRule(
                id="rule_compliance_violation",
                name="Compliance Violation",
                description="Alert on compliance rule breach",
                trigger_condition="compliance_check_failed",
                severity=AlertSeverity.CRITICAL,
                channels=[AlertChannel.IN_APP, AlertChannel.EMAIL, AlertChannel.WEBSOCKET],
                cooldown_minutes=60,
            ),
        ]

    def _initialize_templates(self):
        """Initialize alert templates"""
        self.alert_templates = {
            "stock_alert": AlertTemplate(
                id="stock_alert",
                name="Stock Alert",
                title_template="{product} Stock Critical",
                message_template="{product} is at {current_stock} units. Recommended action: Reorder immediately.",
                category="inventory",
                default_channels=[AlertChannel.IN_APP, AlertChannel.EMAIL],
            ),
            "sales_alert": AlertTemplate(
                id="sales_alert",
                name="Sales Alert",
                title_template="Sales Anomaly Detected",
                message_template="Sales are {deviation}% {direction}. Current: {current_sales}, Expected: {expected_sales}",
                category="sales",
                default_channels=[AlertChannel.IN_APP, AlertChannel.WEBSOCKET],
            ),
            "system_alert": AlertTemplate(
                id="system_alert",
                name="System Alert",
                title_template="System Issue Detected",
                message_template="{system} is experiencing issues. Status: {status}. Availability: {availability}%",
                category="system",
                default_channels=[AlertChannel.IN_APP, AlertChannel.SMS, AlertChannel.EMAIL],
            ),
        }

    async def trigger_alert(self, alert: Alert) -> bool:
        """Trigger a new alert"""
        # Check cooldown
        if self._is_in_cooldown(alert.source):
            return False

        alert.status = AlertStatus.TRIGGERED
        alert.created_at = datetime.now()
        alert.triggered_at = datetime.now()

        self.alerts.append(alert)
        self.alert_history.append(alert)

        # Determine channels based on severity
        if not alert.channels:
            alert.channels = self._get_channels_by_severity(alert.severity)

        # Send via all configured channels
        for channel in alert.channels:
            if channel in self.handlers:
                try:
                    await self.handlers[channel].send(alert)
                except Exception as e:
                    print(f"Error sending alert via {channel}: {e}")
                    alert.retry_count += 1

        # Update last alert time
        self.last_alert_time[alert.source] = datetime.now()

        return True

    def acknowledge_alert(self, alert_id: str, user_id: str) -> Optional[Alert]:
        """Acknowledge an active alert"""
        for alert in self.alerts:
            if alert.id == alert_id:
                alert.status = AlertStatus.ACKNOWLEDGED
                alert.acknowledged_at = datetime.now()
                alert.acknowledged_by = user_id
                return alert

        return None

    def resolve_alert(self, alert_id: str, user_id: str, resolution_notes: str = "") -> Optional[Alert]:
        """Resolve an alert"""
        for alert in self.alerts:
            if alert.id == alert_id:
                alert.status = AlertStatus.RESOLVED
                alert.resolved_at = datetime.now()
                alert.resolved_by = user_id
                self.alerts.remove(alert)
                return alert

        return None

    async def escalate_alert(self, alert_id: str) -> Optional[Alert]:
        """Escalate alert to next level"""
        for alert in self.alerts:
            if alert.id == alert_id and alert.escalation_level < alert.max_escalation:
                alert.escalation_level += 1
                alert.status = AlertStatus.ESCALATED

                # Add more critical channels for escalation
                if AlertChannel.SMS not in alert.channels:
                    alert.channels.append(AlertChannel.SMS)
                if AlertChannel.EMAIL not in alert.channels:
                    alert.channels.append(AlertChannel.EMAIL)

                # Re-send via new channels
                for channel in alert.channels:
                    if channel in self.handlers:
                        try:
                            await self.handlers[channel].send(alert)
                        except Exception as e:
                            print(f"Error escalating alert via {channel}: {e}")

                return alert

        return None

    def get_active_alerts(self) -> List[Alert]:
        """Get all active alerts"""
        return [a for a in self.alerts if a.status in [AlertStatus.TRIGGERED, AlertStatus.ESCALATED]]

    def get_alert_summary(self) -> Dict[str, Any]:
        """Get summary of current alerts"""
        active = self.get_active_alerts()

        summary = {
            "total_active": len(active),
            "by_severity": {
                "info": len([a for a in active if a.severity == AlertSeverity.INFO]),
                "warning": len([a for a in active if a.severity == AlertSeverity.WARNING]),
                "critical": len([a for a in active if a.severity == AlertSeverity.CRITICAL]),
                "emergency": len([a for a in active if a.severity == AlertSeverity.EMERGENCY]),
            },
            "by_category": {},
            "escalated": len([a for a in active if a.escalation_level > 0]),
            "oldest_alert_age_minutes": self._get_oldest_alert_age(),
        }

        # Count by category
        for alert in active:
            category = alert.category
            summary["by_category"][category] = summary["by_category"].get(category, 0) + 1

        return summary

    def _is_in_cooldown(self, source: str) -> bool:
        """Check if source is in cooldown period"""
        if source not in self.last_alert_time:
            return False

        last_time = self.last_alert_time[source]
        cooldown_period = timedelta(minutes=5)  # Default 5 minutes

        return datetime.now() - last_time < cooldown_period

    def _get_channels_by_severity(self, severity: AlertSeverity) -> List[AlertChannel]:
        """Determine alert channels based on severity"""
        if severity == AlertSeverity.EMERGENCY:
            return [AlertChannel.IN_APP, AlertChannel.SMS, AlertChannel.EMAIL, AlertChannel.WEBSOCKET]
        elif severity == AlertSeverity.CRITICAL:
            return [AlertChannel.IN_APP, AlertChannel.EMAIL, AlertChannel.WEBSOCKET]
        elif severity == AlertSeverity.WARNING:
            return [AlertChannel.IN_APP, AlertChannel.WEBSOCKET]
        else:  # INFO
            return [AlertChannel.IN_APP]

    def _get_oldest_alert_age(self) -> int:
        """Get age of oldest active alert in minutes"""
        active = self.get_active_alerts()
        if not active:
            return 0

        oldest = min(active, key=lambda a: a.triggered_at)
        age = datetime.now() - oldest.triggered_at
        return int(age.total_seconds() / 60)


# Initialize global alert system
jarvis_alert_system = JarvisAlertSystem()
