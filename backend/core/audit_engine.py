"""
IMS 2.0 - Audit Engine
=======================
Complete audit trail for all system activities

Features:
1. Activity Logging (who, what, when, where)
2. Change History
3. Sensitive Data Access Logging
4. Superadmin Audit Dashboard
"""
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from enum import Enum
from typing import List, Optional, Dict, Any
import uuid
import json

class AuditAction(Enum):
    # Auth
    LOGIN = "LOGIN"
    LOGOUT = "LOGOUT"
    LOGIN_FAILED = "LOGIN_FAILED"
    PASSWORD_CHANGE = "PASSWORD_CHANGE"
    
    # Data Operations
    CREATE = "CREATE"
    READ = "READ"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    
    # Business Operations
    SALE_CREATED = "SALE_CREATED"
    SALE_VOIDED = "SALE_VOIDED"
    PAYMENT_RECEIVED = "PAYMENT_RECEIVED"
    REFUND_ISSUED = "REFUND_ISSUED"
    DISCOUNT_APPLIED = "DISCOUNT_APPLIED"
    DISCOUNT_OVERRIDE = "DISCOUNT_OVERRIDE"
    
    # Inventory
    STOCK_ADDED = "STOCK_ADDED"
    STOCK_TRANSFERRED = "STOCK_TRANSFERRED"
    STOCK_ADJUSTED = "STOCK_ADJUSTED"
    STOCK_COUNT = "STOCK_COUNT"
    
    # Clinical
    PRESCRIPTION_CREATED = "PRESCRIPTION_CREATED"
    PRESCRIPTION_OVERRIDE = "PRESCRIPTION_OVERRIDE"
    
    # Finance
    TILL_OPENED = "TILL_OPENED"
    TILL_CLOSED = "TILL_CLOSED"
    EXPENSE_APPROVED = "EXPENSE_APPROVED"
    
    # Admin
    USER_CREATED = "USER_CREATED"
    USER_MODIFIED = "USER_MODIFIED"
    ROLE_CHANGED = "ROLE_CHANGED"
    SETTING_CHANGED = "SETTING_CHANGED"
    
    # AI
    AI_INSIGHT_VIEWED = "AI_INSIGHT_VIEWED"
    AI_RECOMMENDATION_APPROVED = "AI_RECOMMENDATION_APPROVED"

class AuditSeverity(Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"

@dataclass
class AuditLog:
    id: str
    timestamp: datetime
    
    # Who
    user_id: str
    user_name: str
    user_role: str
    
    # Where
    store_id: str
    store_name: str
    
    # What
    action: AuditAction
    module: str
    entity_type: str
    entity_id: str
    
    # Optional fields with defaults
    ip_address: str = ""
    device_info: str = ""
    entity_name: str = ""
    
    # Details
    severity: AuditSeverity = AuditSeverity.INFO
    description: str = ""
    
    # Change tracking
    previous_value: Optional[str] = None
    new_value: Optional[str] = None
    changed_fields: List[str] = field(default_factory=list)
    
    # Additional context
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class SensitiveAccessLog:
    id: str
    timestamp: datetime
    user_id: str
    user_role: str
    data_type: str  # SALARY, PRESCRIPTION, CUSTOMER_PII, etc.
    entity_id: str
    reason: str
    approved_by: Optional[str] = None

@dataclass
class AuditSummary:
    date: date
    store_id: str
    total_actions: int = 0
    logins: int = 0
    sales: int = 0
    stock_movements: int = 0
    overrides: int = 0
    warnings: int = 0
    critical: int = 0


class AuditEngine:
    """
    Comprehensive Audit Trail System
    """
    
    def __init__(self):
        self.logs: Dict[str, AuditLog] = {}
        self.sensitive_logs: Dict[str, SensitiveAccessLog] = {}
        self.daily_summaries: Dict[str, AuditSummary] = {}
    
    def log(
        self,
        action: AuditAction,
        user_id: str,
        user_name: str,
        user_role: str,
        store_id: str,
        store_name: str,
        module: str,
        entity_type: str,
        entity_id: str,
        entity_name: str = "",
        description: str = "",
        severity: AuditSeverity = AuditSeverity.INFO,
        previous_value: Any = None,
        new_value: Any = None,
        changed_fields: List[str] = None,
        ip_address: str = "",
        metadata: Dict = None
    ) -> AuditLog:
        """Create audit log entry"""
        
        log = AuditLog(
            id=str(uuid.uuid4()),
            timestamp=datetime.now(),
            user_id=user_id,
            user_name=user_name,
            user_role=user_role,
            store_id=store_id,
            store_name=store_name,
            action=action,
            module=module,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_name=entity_name,
            description=description,
            severity=severity,
            previous_value=json.dumps(previous_value) if previous_value else None,
            new_value=json.dumps(new_value) if new_value else None,
            changed_fields=changed_fields or [],
            ip_address=ip_address,
            metadata=metadata or {}
        )
        
        self.logs[log.id] = log
        self._update_summary(log)
        
        return log
    
    def _update_summary(self, log: AuditLog):
        """Update daily summary"""
        key = f"{log.store_id}_{log.timestamp.date()}"
        
        if key not in self.daily_summaries:
            self.daily_summaries[key] = AuditSummary(
                date=log.timestamp.date(),
                store_id=log.store_id
            )
        
        summary = self.daily_summaries[key]
        summary.total_actions += 1
        
        if log.action == AuditAction.LOGIN:
            summary.logins += 1
        elif log.action == AuditAction.SALE_CREATED:
            summary.sales += 1
        elif log.action in [AuditAction.STOCK_ADDED, AuditAction.STOCK_TRANSFERRED]:
            summary.stock_movements += 1
        elif "OVERRIDE" in log.action.value:
            summary.overrides += 1
        
        if log.severity == AuditSeverity.WARNING:
            summary.warnings += 1
        elif log.severity == AuditSeverity.CRITICAL:
            summary.critical += 1
    
    def log_sensitive_access(
        self,
        user_id: str,
        user_role: str,
        data_type: str,
        entity_id: str,
        reason: str,
        approved_by: str = None
    ) -> SensitiveAccessLog:
        """Log access to sensitive data"""
        
        log = SensitiveAccessLog(
            id=str(uuid.uuid4()),
            timestamp=datetime.now(),
            user_id=user_id,
            user_role=user_role,
            data_type=data_type,
            entity_id=entity_id,
            reason=reason,
            approved_by=approved_by
        )
        
        self.sensitive_logs[log.id] = log
        return log
    
    # Convenience methods for common actions
    def log_login(self, user_id: str, user_name: str, user_role: str, 
                  store_id: str, store_name: str, ip: str, success: bool = True) -> AuditLog:
        return self.log(
            AuditAction.LOGIN if success else AuditAction.LOGIN_FAILED,
            user_id, user_name, user_role, store_id, store_name,
            "AUTH", "USER", user_id, user_name,
            f"Login {'successful' if success else 'failed'}",
            AuditSeverity.INFO if success else AuditSeverity.WARNING,
            ip_address=ip
        )
    
    def log_sale(self, user_id: str, user_name: str, user_role: str,
                 store_id: str, store_name: str, order_id: str, order_number: str,
                 amount: float) -> AuditLog:
        return self.log(
            AuditAction.SALE_CREATED,
            user_id, user_name, user_role, store_id, store_name,
            "POS", "ORDER", order_id, order_number,
            f"Sale of ₹{amount:,.2f}",
            metadata={"amount": amount}
        )
    
    def log_discount_override(self, user_id: str, user_name: str, user_role: str,
                               store_id: str, store_name: str, order_id: str,
                               original_discount: float, new_discount: float,
                               approved_by: str) -> AuditLog:
        return self.log(
            AuditAction.DISCOUNT_OVERRIDE,
            user_id, user_name, user_role, store_id, store_name,
            "POS", "ORDER", order_id, "",
            f"Discount override: {original_discount}% → {new_discount}%",
            AuditSeverity.WARNING,
            previous_value=original_discount,
            new_value=new_discount,
            metadata={"approved_by": approved_by}
        )
    
    def log_stock_adjustment(self, user_id: str, user_name: str, user_role: str,
                              store_id: str, store_name: str, product_id: str, product_name: str,
                              old_qty: int, new_qty: int, reason: str) -> AuditLog:
        return self.log(
            AuditAction.STOCK_ADJUSTED,
            user_id, user_name, user_role, store_id, store_name,
            "INVENTORY", "PRODUCT", product_id, product_name,
            f"Stock adjusted: {old_qty} → {new_qty}. Reason: {reason}",
            AuditSeverity.WARNING,
            previous_value=old_qty,
            new_value=new_qty,
            metadata={"reason": reason}
        )
    
    def log_setting_change(self, user_id: str, user_name: str, user_role: str,
                           setting_key: str, old_value: Any, new_value: Any) -> AuditLog:
        return self.log(
            AuditAction.SETTING_CHANGED,
            user_id, user_name, user_role, "HQ", "Headquarters",
            "SETTINGS", "CONFIG", setting_key, setting_key,
            f"Setting changed: {setting_key}",
            AuditSeverity.CRITICAL,
            previous_value=old_value,
            new_value=new_value
        )
    
    # Query methods
    def get_logs(self, store_id: str = None, user_id: str = None, 
                 action: AuditAction = None, from_date: date = None,
                 to_date: date = None, severity: AuditSeverity = None,
                 limit: int = 100) -> List[AuditLog]:
        """Query audit logs with filters"""
        
        logs = list(self.logs.values())
        
        if store_id:
            logs = [l for l in logs if l.store_id == store_id]
        if user_id:
            logs = [l for l in logs if l.user_id == user_id]
        if action:
            logs = [l for l in logs if l.action == action]
        if severity:
            logs = [l for l in logs if l.severity == severity]
        if from_date:
            logs = [l for l in logs if l.timestamp.date() >= from_date]
        if to_date:
            logs = [l for l in logs if l.timestamp.date() <= to_date]
        
        return sorted(logs, key=lambda x: x.timestamp, reverse=True)[:limit]
    
    def get_entity_history(self, entity_type: str, entity_id: str) -> List[AuditLog]:
        """Get all changes to an entity"""
        return [
            l for l in self.logs.values()
            if l.entity_type == entity_type and l.entity_id == entity_id
        ]
    
    def get_user_activity(self, user_id: str, days: int = 7) -> Dict:
        """Get user activity summary"""
        cutoff = datetime.now() - timedelta(days=days)
        logs = [l for l in self.logs.values() if l.user_id == user_id and l.timestamp >= cutoff]
        
        return {
            "total_actions": len(logs),
            "by_action": {a.value: len([l for l in logs if l.action == a]) for a in AuditAction},
            "warnings": len([l for l in logs if l.severity == AuditSeverity.WARNING]),
            "critical": len([l for l in logs if l.severity == AuditSeverity.CRITICAL])
        }
    
    def get_daily_summary(self, store_id: str, dt: date) -> Optional[AuditSummary]:
        """Get daily summary for store"""
        return self.daily_summaries.get(f"{store_id}_{dt}")
    
    def get_superadmin_dashboard(self) -> Dict:
        """Get audit dashboard for superadmin"""
        today = date.today()
        all_logs = list(self.logs.values())
        today_logs = [l for l in all_logs if l.timestamp.date() == today]
        
        return {
            "total_logs": len(all_logs),
            "today": {
                "total": len(today_logs),
                "logins": len([l for l in today_logs if l.action == AuditAction.LOGIN]),
                "sales": len([l for l in today_logs if l.action == AuditAction.SALE_CREATED]),
                "overrides": len([l for l in today_logs if "OVERRIDE" in l.action.value]),
                "warnings": len([l for l in today_logs if l.severity == AuditSeverity.WARNING]),
                "critical": len([l for l in today_logs if l.severity == AuditSeverity.CRITICAL])
            },
            "sensitive_access": len(self.sensitive_logs),
            "stores_active": len(set(l.store_id for l in today_logs))
        }


def demo_audit():
    print("=" * 60)
    print("IMS 2.0 AUDIT ENGINE DEMO")
    print("=" * 60)
    
    engine = AuditEngine()
    
    # Log login
    print("\n🔐 Log Login")
    log = engine.log_login("user-001", "Rahul Singh", "SALES_STAFF", 
                           "store-001", "Bokaro", "192.168.1.100")
    print(f"  Logged: {log.action.value} - {log.user_name}")
    
    # Log sale
    print("\n🛒 Log Sale")
    log = engine.log_sale("user-001", "Rahul Singh", "SALES_STAFF",
                          "store-001", "Bokaro", "order-001", "BV/ORD/001", 15000)
    print(f"  Logged: {log.description}")
    
    # Log discount override
    print("\n⚠️ Log Discount Override")
    log = engine.log_discount_override("user-001", "Rahul Singh", "SALES_STAFF",
                                        "store-001", "Bokaro", "order-001",
                                        10, 15, "mgr-001")
    print(f"  Logged: {log.description} - Severity: {log.severity.value}")
    
    # Log stock adjustment
    print("\n📦 Log Stock Adjustment")
    log = engine.log_stock_adjustment("mgr-001", "Store Manager", "STORE_MANAGER",
                                       "store-001", "Bokaro", "prod-001", "Ray-Ban RB5154",
                                       50, 48, "Damaged items")
    print(f"  Logged: {log.description}")
    
    # Log sensitive access
    print("\n🔒 Log Sensitive Access")
    sens_log = engine.log_sensitive_access("hr-001", "HR_MANAGER", "SALARY",
                                            "emp-001", "Salary review")
    print(f"  Logged: Access to {sens_log.data_type}")
    
    # Query logs
    print("\n🔍 Query Logs")
    logs = engine.get_logs(store_id="store-001", limit=5)
    print(f"  Found {len(logs)} logs for store-001")
    
    # User activity
    print("\n👤 User Activity")
    activity = engine.get_user_activity("user-001")
    print(f"  Total actions: {activity['total_actions']}")
    print(f"  Warnings: {activity['warnings']}")
    
    # Superadmin dashboard
    print("\n📊 Superadmin Dashboard")
    dashboard = engine.get_superadmin_dashboard()
    print(f"  Total logs: {dashboard['total_logs']}")
    print(f"  Today's actions: {dashboard['today']['total']}")
    print(f"  Warnings today: {dashboard['today']['warnings']}")
    
    print("\n" + "=" * 60)


if __name__ == "__main__":
    demo_audit()
