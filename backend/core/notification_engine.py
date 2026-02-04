"""
IMS 2.0 - Notification Engine
==============================
Alerts, reminders, and notifications

Features:
1. Push Notifications
2. SMS/WhatsApp/Email Triggers
3. Scheduled Reminders
4. Escalation Notifications
"""
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from enum import Enum
from typing import List, Optional, Dict, Callable
import uuid

class NotificationChannel(Enum):
    IN_APP = "IN_APP"
    SMS = "SMS"
    EMAIL = "EMAIL"
    WHATSAPP = "WHATSAPP"
    PUSH = "PUSH"

class NotificationPriority(Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"

class NotificationStatus(Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    READ = "READ"
    FAILED = "FAILED"

class NotificationType(Enum):
    # Orders
    ORDER_CREATED = "ORDER_CREATED"
    ORDER_READY = "ORDER_READY"
    ORDER_DELIVERED = "ORDER_DELIVERED"
    
    # Tasks
    TASK_ASSIGNED = "TASK_ASSIGNED"
    TASK_DUE = "TASK_DUE"
    TASK_OVERDUE = "TASK_OVERDUE"
    TASK_ESCALATED = "TASK_ESCALATED"
    
    # Inventory
    LOW_STOCK = "LOW_STOCK"
    STOCK_EXPIRY = "STOCK_EXPIRY"
    STOCK_TRANSFER = "STOCK_TRANSFER"
    GRN_MISMATCH = "GRN_MISMATCH"
    
    # Approvals
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_GRANTED = "APPROVAL_GRANTED"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    
    # Finance
    PAYMENT_DUE = "PAYMENT_DUE"
    TILL_VARIANCE = "TILL_VARIANCE"
    
    # HR
    LEAVE_REQUEST = "LEAVE_REQUEST"
    ATTENDANCE_ALERT = "ATTENDANCE_ALERT"
    
    # System
    SYSTEM_ALERT = "SYSTEM_ALERT"
    REMINDER = "REMINDER"

@dataclass
class Notification:
    id: str
    notification_type: NotificationType
    
    # Recipient
    user_id: str
    user_name: str
    
    # Content
    title: str
    message: str
    
    # Metadata
    entity_type: str = ""
    entity_id: str = ""
    action_url: str = ""
    
    # Delivery
    channels: List[NotificationChannel] = field(default_factory=lambda: [NotificationChannel.IN_APP])
    priority: NotificationPriority = NotificationPriority.NORMAL
    
    # Status
    status: NotificationStatus = NotificationStatus.PENDING
    sent_at: Optional[datetime] = None
    read_at: Optional[datetime] = None
    
    # Scheduling
    scheduled_for: Optional[datetime] = None
    
    created_at: datetime = field(default_factory=datetime.now)

@dataclass
class NotificationTemplate:
    id: str
    notification_type: NotificationType
    name: str
    
    # Templates
    title_template: str
    message_template: str
    sms_template: str = ""
    email_subject: str = ""
    email_body: str = ""
    whatsapp_template_id: str = ""
    
    # Settings
    default_channels: List[NotificationChannel] = field(default_factory=lambda: [NotificationChannel.IN_APP])
    default_priority: NotificationPriority = NotificationPriority.NORMAL
    
    is_active: bool = True

@dataclass
class ScheduledReminder:
    id: str
    reminder_type: str
    user_id: str
    scheduled_time: datetime
    title: str
    message: str
    
    # Optional with defaults
    repeat: str = "ONCE"  # ONCE, DAILY, WEEKLY
    is_completed: bool = False
    last_sent: Optional[datetime] = None
    next_send: Optional[datetime] = None


class NotificationEngine:
    """
    Notification Management Engine
    """
    
    def __init__(self):
        self.notifications: Dict[str, Notification] = {}
        self.templates: Dict[NotificationType, NotificationTemplate] = {}
        self.reminders: Dict[str, ScheduledReminder] = {}
        self._sms_handler: Optional[Callable] = None
        self._email_handler: Optional[Callable] = None
        self._whatsapp_handler: Optional[Callable] = None
        self._push_handler: Optional[Callable] = None
        
        self._initialize_templates()
    
    def _initialize_templates(self):
        """Initialize default notification templates"""
        templates = [
            NotificationTemplate(
                str(uuid.uuid4()), NotificationType.ORDER_READY,
                "Order Ready",
                "Order Ready for Pickup",
                "Your order {order_number} is ready for collection at {store_name}.",
                sms_template="Your order {order_number} is ready at Better Vision {store_name}.",
                whatsapp_template_id="order_ready_v1",
                default_channels=[NotificationChannel.IN_APP, NotificationChannel.WHATSAPP, NotificationChannel.SMS]
            ),
            NotificationTemplate(
                str(uuid.uuid4()), NotificationType.TASK_ASSIGNED,
                "Task Assigned",
                "New Task Assigned",
                "You have been assigned a new task: {task_title}. Due: {due_date}",
                default_channels=[NotificationChannel.IN_APP, NotificationChannel.PUSH],
                default_priority=NotificationPriority.HIGH
            ),
            NotificationTemplate(
                str(uuid.uuid4()), NotificationType.TASK_OVERDUE,
                "Task Overdue",
                "⚠️ Task Overdue",
                "Task '{task_title}' is overdue. Please complete immediately.",
                default_channels=[NotificationChannel.IN_APP, NotificationChannel.PUSH],
                default_priority=NotificationPriority.URGENT
            ),
            NotificationTemplate(
                str(uuid.uuid4()), NotificationType.LOW_STOCK,
                "Low Stock Alert",
                "Low Stock Alert",
                "{product_name} is running low. Current stock: {current_qty}. Reorder point: {reorder_point}",
                default_channels=[NotificationChannel.IN_APP],
                default_priority=NotificationPriority.HIGH
            ),
            NotificationTemplate(
                str(uuid.uuid4()), NotificationType.APPROVAL_REQUIRED,
                "Approval Required",
                "Approval Required",
                "{requester_name} needs your approval for {request_type}: {request_details}",
                default_channels=[NotificationChannel.IN_APP, NotificationChannel.PUSH],
                default_priority=NotificationPriority.HIGH
            ),
            NotificationTemplate(
                str(uuid.uuid4()), NotificationType.TASK_ESCALATED,
                "Task Escalated",
                "🔴 Task Escalated",
                "Task '{task_title}' has been escalated to you. Original assignee: {original_assignee}",
                default_channels=[NotificationChannel.IN_APP, NotificationChannel.PUSH, NotificationChannel.SMS],
                default_priority=NotificationPriority.URGENT
            ),
        ]
        
        for t in templates:
            self.templates[t.notification_type] = t
    
    def set_handlers(self, sms=None, email=None, whatsapp=None, push=None):
        """Set channel handlers"""
        self._sms_handler = sms
        self._email_handler = email
        self._whatsapp_handler = whatsapp
        self._push_handler = push
    
    def create_notification(
        self,
        notification_type: NotificationType,
        user_id: str,
        user_name: str,
        data: Dict,
        entity_type: str = "",
        entity_id: str = "",
        channels: List[NotificationChannel] = None,
        priority: NotificationPriority = None,
        schedule_for: datetime = None
    ) -> Notification:
        """Create notification using template"""
        
        template = self.templates.get(notification_type)
        
        # Format message with data
        title = template.title_template.format(**data) if template else data.get("title", "Notification")
        message = template.message_template.format(**data) if template else data.get("message", "")
        
        notif = Notification(
            id=str(uuid.uuid4()),
            notification_type=notification_type,
            user_id=user_id,
            user_name=user_name,
            title=title,
            message=message,
            entity_type=entity_type,
            entity_id=entity_id,
            channels=channels or (template.default_channels if template else [NotificationChannel.IN_APP]),
            priority=priority or (template.default_priority if template else NotificationPriority.NORMAL),
            scheduled_for=schedule_for
        )
        
        self.notifications[notif.id] = notif
        
        # Send immediately if not scheduled
        if not schedule_for:
            self._send_notification(notif)
        
        return notif
    
    def _send_notification(self, notif: Notification):
        """Send notification through channels"""
        for channel in notif.channels:
            try:
                if channel == NotificationChannel.SMS and self._sms_handler:
                    self._sms_handler(notif)
                elif channel == NotificationChannel.EMAIL and self._email_handler:
                    self._email_handler(notif)
                elif channel == NotificationChannel.WHATSAPP and self._whatsapp_handler:
                    self._whatsapp_handler(notif)
                elif channel == NotificationChannel.PUSH and self._push_handler:
                    self._push_handler(notif)
            except Exception as e:
                notif.status = NotificationStatus.FAILED
                return
        
        notif.status = NotificationStatus.SENT
        notif.sent_at = datetime.now()
    
    # Convenience methods
    def notify_order_ready(self, user_id: str, user_name: str, order_number: str, store_name: str):
        return self.create_notification(
            NotificationType.ORDER_READY,
            user_id, user_name,
            {"order_number": order_number, "store_name": store_name},
            "ORDER", order_number
        )
    
    def notify_task_assigned(self, user_id: str, user_name: str, task_id: str, 
                             task_title: str, due_date: str):
        return self.create_notification(
            NotificationType.TASK_ASSIGNED,
            user_id, user_name,
            {"task_title": task_title, "due_date": due_date},
            "TASK", task_id
        )
    
    def notify_task_escalated(self, user_id: str, user_name: str, task_id: str,
                               task_title: str, original_assignee: str):
        return self.create_notification(
            NotificationType.TASK_ESCALATED,
            user_id, user_name,
            {"task_title": task_title, "original_assignee": original_assignee},
            "TASK", task_id
        )
    
    def notify_low_stock(self, user_id: str, user_name: str, product_id: str,
                         product_name: str, current_qty: int, reorder_point: int):
        return self.create_notification(
            NotificationType.LOW_STOCK,
            user_id, user_name,
            {"product_name": product_name, "current_qty": current_qty, "reorder_point": reorder_point},
            "PRODUCT", product_id
        )
    
    def notify_approval_required(self, user_id: str, user_name: str,
                                  requester_name: str, request_type: str, 
                                  request_details: str, entity_id: str):
        return self.create_notification(
            NotificationType.APPROVAL_REQUIRED,
            user_id, user_name,
            {"requester_name": requester_name, "request_type": request_type, "request_details": request_details},
            "APPROVAL", entity_id
        )
    
    # Reminders
    def create_reminder(self, user_id: str, title: str, message: str,
                        scheduled_time: datetime, repeat: str = "ONCE") -> ScheduledReminder:
        reminder = ScheduledReminder(
            id=str(uuid.uuid4()),
            reminder_type="CUSTOM",
            user_id=user_id,
            scheduled_time=scheduled_time,
            repeat=repeat,
            title=title,
            message=message,
            next_send=scheduled_time
        )
        self.reminders[reminder.id] = reminder
        return reminder
    
    def process_scheduled_reminders(self):
        """Process and send due reminders"""
        now = datetime.now()
        due_reminders = [
            r for r in self.reminders.values()
            if not r.is_completed and r.next_send and r.next_send <= now
        ]
        
        for reminder in due_reminders:
            self.create_notification(
                NotificationType.REMINDER,
                reminder.user_id, "",
                {"title": reminder.title, "message": reminder.message}
            )
            
            reminder.last_sent = now
            
            if reminder.repeat == "ONCE":
                reminder.is_completed = True
            elif reminder.repeat == "DAILY":
                reminder.next_send = now + timedelta(days=1)
            elif reminder.repeat == "WEEKLY":
                reminder.next_send = now + timedelta(weeks=1)
    
    # Queries
    def get_user_notifications(self, user_id: str, unread_only: bool = False) -> List[Notification]:
        notifs = [n for n in self.notifications.values() if n.user_id == user_id]
        if unread_only:
            notifs = [n for n in notifs if n.status != NotificationStatus.READ]
        return sorted(notifs, key=lambda x: x.created_at, reverse=True)
    
    def mark_as_read(self, notification_id: str):
        notif = self.notifications.get(notification_id)
        if notif:
            notif.status = NotificationStatus.READ
            notif.read_at = datetime.now()
    
    def get_unread_count(self, user_id: str) -> int:
        return len([n for n in self.notifications.values() 
                   if n.user_id == user_id and n.status != NotificationStatus.READ])


def demo_notifications():
    print("=" * 60)
    print("IMS 2.0 NOTIFICATION ENGINE DEMO")
    print("=" * 60)
    
    engine = NotificationEngine()
    
    # Order ready notification
    print("\n📦 Order Ready Notification")
    notif = engine.notify_order_ready("cust-001", "Rajesh Kumar", "BV/ORD/001", "Bokaro")
    print(f"  Title: {notif.title}")
    print(f"  Message: {notif.message}")
    print(f"  Channels: {[c.value for c in notif.channels]}")
    
    # Task assigned
    print("\n📋 Task Assigned Notification")
    notif = engine.notify_task_assigned("staff-001", "Rahul Singh", "task-001",
                                         "Stock Count", "2026-01-22")
    print(f"  Title: {notif.title}")
    print(f"  Priority: {notif.priority.value}")
    
    # Task escalated
    print("\n🔴 Task Escalated Notification")
    notif = engine.notify_task_escalated("mgr-001", "Store Manager", "task-002",
                                          "GRN Verification", "Rahul Singh")
    print(f"  Title: {notif.title}")
    print(f"  Priority: {notif.priority.value}")
    
    # Low stock
    print("\n⚠️ Low Stock Notification")
    notif = engine.notify_low_stock("mgr-001", "Store Manager", "prod-001",
                                     "Ray-Ban RB5154", 5, 10)
    print(f"  Message: {notif.message}")
    
    # Approval required
    print("\n✋ Approval Required Notification")
    notif = engine.notify_approval_required("mgr-001", "Store Manager",
                                             "Rahul Singh", "Discount Override",
                                             "15% discount on ₹10,000 order", "order-001")
    print(f"  Message: {notif.message}")
    
    # Create reminder
    print("\n⏰ Create Reminder")
    reminder = engine.create_reminder("mgr-001", "Daily Till Close",
                                       "Remember to close till before leaving",
                                       datetime.now() + timedelta(hours=8), "DAILY")
    print(f"  Reminder: {reminder.title}")
    print(f"  Repeat: {reminder.repeat}")
    
    # User notifications
    print("\n📬 User Notifications")
    notifs = engine.get_user_notifications("mgr-001")
    print(f"  Manager has {len(notifs)} notifications")
    print(f"  Unread: {engine.get_unread_count('mgr-001')}")
    
    print("\n" + "=" * 60)


if __name__ == "__main__":
    demo_notifications()
