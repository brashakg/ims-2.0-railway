# ============================================================================
# IMS 2.0 - Comprehensive Audit Logging System
# ============================================================================

from datetime import datetime
from typing import Optional, Dict, Any
from enum import Enum
import json
from sqlalchemy import create_engine, Column, String, DateTime, JSON, Integer
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import hashlib

Base = declarative_base()

# ============================================================================
# Enums
# ============================================================================


class AuditAction(str, Enum):
    """Audit log actions"""

    # Authentication
    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILURE = "login_failure"
    LOGOUT = "logout"
    PASSWORD_CHANGED = "password_changed"
    PASSWORD_RESET = "password_reset"
    TWO_FA_ENABLED = "2fa_enabled"
    TWO_FA_DISABLED = "2fa_disabled"

    # User Management
    USER_CREATED = "user_created"
    USER_UPDATED = "user_updated"
    USER_DELETED = "user_deleted"
    USER_ROLE_CHANGED = "user_role_changed"
    USER_PERMISSION_GRANTED = "user_permission_granted"
    USER_PERMISSION_REVOKED = "user_permission_revoked"

    # Data Operations
    DATA_CREATED = "data_created"
    DATA_UPDATED = "data_updated"
    DATA_DELETED = "data_deleted"
    DATA_EXPORTED = "data_exported"
    DATA_IMPORTED = "data_imported"

    # Financial Transactions
    PAYMENT_PROCESSED = "payment_processed"
    REFUND_ISSUED = "refund_issued"
    INVOICE_GENERATED = "invoice_generated"

    # System Administration
    CONFIGURATION_CHANGED = "configuration_changed"
    BACKUP_CREATED = "backup_created"
    SYSTEM_UPDATE = "system_update"

    # Security Events
    UNAUTHORIZED_ACCESS = "unauthorized_access"
    SUSPICIOUS_ACTIVITY = "suspicious_activity"
    PERMISSION_DENIED = "permission_denied"
    SECURITY_ALERT = "security_alert"


class AuditSeverity(str, Enum):
    """Audit event severity levels"""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


# ============================================================================
# Database Model
# ============================================================================


class AuditLog(Base):
    """Audit log database model"""

    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    user_id = Column(String(255), nullable=True, index=True)
    action = Column(String(100), nullable=False, index=True)
    resource = Column(String(100), nullable=False)
    resource_id = Column(String(255), nullable=True, index=True)
    status = Column(String(20), nullable=False)  # success, failure
    severity = Column(String(20), default=AuditSeverity.INFO)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(500), nullable=True)
    details = Column(JSON, nullable=True)
    change_hash = Column(String(64), nullable=True)  # For change verification


# ============================================================================
# Audit Logger Service
# ============================================================================


class AuditLoggerService:
    """Comprehensive audit logging service"""

    def __init__(self, database_url: str):
        self.engine = create_engine(database_url)
        self.SessionLocal = sessionmaker(bind=self.engine)
        Base.metadata.create_all(self.engine)

    async def log_event(
        self,
        action: AuditAction,
        user_id: Optional[str] = None,
        resource: Optional[str] = None,
        resource_id: Optional[str] = None,
        status: str = "success",
        severity: AuditSeverity = AuditSeverity.INFO,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        before_state: Optional[Dict] = None,
        after_state: Optional[Dict] = None,
    ):
        """Log an audit event"""

        # Calculate change hash if before/after states provided
        change_hash = None
        if before_state and after_state:
            change_hash = self._calculate_change_hash(before_state, after_state)

        # Sanitize details (remove sensitive data)
        sanitized_details = self._sanitize_details(details) if details else {}

        log_entry = AuditLog(
            timestamp=datetime.utcnow(),
            user_id=user_id,
            action=action.value,
            resource=resource,
            resource_id=resource_id,
            status=status,
            severity=severity.value,
            ip_address=ip_address,
            user_agent=user_agent,
            details=sanitized_details,
            change_hash=change_hash,
        )

        # Save to database
        session = self.SessionLocal()
        try:
            session.add(log_entry)
            session.commit()
            session.refresh(log_entry)

            # Also log to structured logging system
            self._log_to_structured_logger(log_entry)

            return log_entry

        except Exception as e:
            session.rollback()
            # Log to failsafe logging (stdout, file, etc.)
            self._log_to_failsafe(log_entry, str(e))
            raise

        finally:
            session.close()

    async def get_user_activity(self, user_id: str, limit: int = 100, offset: int = 0):
        """Get audit logs for specific user"""

        session = self.SessionLocal()
        try:
            logs = (
                session.query(AuditLog)
                .filter(AuditLog.user_id == user_id)
                .order_by(AuditLog.timestamp.desc())
                .limit(limit)
                .offset(offset)
                .all()
            )

            return logs

        finally:
            session.close()

    async def get_resource_changes(self, resource: str, resource_id: str):
        """Get change history for a resource"""

        session = self.SessionLocal()
        try:
            logs = (
                session.query(AuditLog)
                .filter(
                    AuditLog.resource == resource, AuditLog.resource_id == resource_id
                )
                .order_by(AuditLog.timestamp.asc())
                .all()
            )

            return logs

        finally:
            session.close()

    async def verify_audit_trail(self, log_id: int, expected_hash: str) -> bool:
        """Verify audit trail integrity using cryptographic hash"""

        session = self.SessionLocal()
        try:
            log = session.query(AuditLog).filter(AuditLog.id == log_id).first()

            if not log:
                return False

            # Verify hash matches
            return log.change_hash == expected_hash

        finally:
            session.close()

    @staticmethod
    def _calculate_change_hash(before_state: Dict, after_state: Dict) -> str:
        """Calculate cryptographic hash of changes"""

        change_data = {
            "before": before_state,
            "after": after_state,
            "timestamp": datetime.utcnow().isoformat(),
        }

        change_json = json.dumps(change_data, sort_keys=True)
        return hashlib.sha256(change_json.encode()).hexdigest()

    @staticmethod
    def _sanitize_details(details: Dict) -> Dict:
        """Remove sensitive data from audit details"""

        sensitive_fields = {
            "password",
            "token",
            "secret",
            "key",
            "credit_card",
            "ssn",
            "pin",
            "api_key",
            "private_key",
        }

        sanitized = {}
        for key, value in details.items():
            if any(sensitive in key.lower() for sensitive in sensitive_fields):
                sanitized[key] = "***REDACTED***"
            elif isinstance(value, dict):
                sanitized[key] = AuditLoggerService._sanitize_details(value)
            else:
                sanitized[key] = value

        return sanitized

    @staticmethod
    def _log_to_structured_logger(log_entry: AuditLog):
        """Log to structured logging system (ELK, DataDog, etc.)"""

        log_dict = {
            "timestamp": log_entry.timestamp.isoformat(),
            "user_id": log_entry.user_id,
            "action": log_entry.action,
            "resource": log_entry.resource,
            "resource_id": log_entry.resource_id,
            "status": log_entry.status,
            "severity": log_entry.severity,
            "ip_address": log_entry.ip_address,
            "details": log_entry.details,
        }

        # Send to structured logging service
        # e.g., elasticsearch, datadog, splunk
        # logging.info(json.dumps(log_dict))

    @staticmethod
    def _log_to_failsafe(log_entry: AuditLog, error: str):
        """Failsafe logging to ensure events are recorded"""

        # Write to local file as backup
        with open("/var/log/ims/audit_failsafe.log", "a") as f:
            f.write(
                json.dumps(
                    {
                        "timestamp": datetime.utcnow().isoformat(),
                        "action": log_entry.action,
                        "error": error,
                    }
                )
                + "\n"
            )


# ============================================================================
# Compliance Reports
# ============================================================================


class ComplianceReportService:
    """Generate compliance reports from audit logs"""

    @staticmethod
    async def generate_user_access_report(
        user_id: str, start_date: datetime, end_date: datetime
    ) -> Dict:
        """Generate user access report for compliance"""

        audit_service = AuditLoggerService(DATABASE_URL)

        session = audit_service.SessionLocal()
        try:
            logs = (
                session.query(AuditLog)
                .filter(
                    AuditLog.user_id == user_id,
                    AuditLog.timestamp >= start_date,
                    AuditLog.timestamp <= end_date,
                )
                .all()
            )

            return {
                "user_id": user_id,
                "period": {
                    "start": start_date.isoformat(),
                    "end": end_date.isoformat(),
                },
                "total_actions": len(logs),
                "actions_by_type": self._group_by_action(logs),
                "login_history": self._extract_login_history(logs),
                "sensitive_operations": self._extract_sensitive_ops(logs),
            }

        finally:
            session.close()

    @staticmethod
    def _group_by_action(logs: list) -> Dict:
        """Group audit logs by action type"""

        grouped = {}
        for log in logs:
            if log.action not in grouped:
                grouped[log.action] = 0
            grouped[log.action] += 1

        return grouped

    @staticmethod
    def _extract_login_history(logs: list) -> list:
        """Extract login history from logs"""

        login_logs = [
            log for log in logs if log.action in ["login_success", "login_failure"]
        ]

        return [
            {
                "timestamp": log.timestamp.isoformat(),
                "action": log.action,
                "ip_address": log.ip_address,
                "user_agent": log.user_agent,
            }
            for log in login_logs
        ]

    @staticmethod
    def _extract_sensitive_ops(logs: list) -> list:
        """Extract sensitive operations"""

        sensitive_actions = {
            "user_deleted",
            "data_deleted",
            "payment_processed",
            "user_role_changed",
            "configuration_changed",
        }

        sensitive_logs = [log for log in logs if log.action in sensitive_actions]

        return [
            {
                "timestamp": log.timestamp.isoformat(),
                "action": log.action,
                "resource": log.resource,
                "resource_id": log.resource_id,
                "details": log.details,
            }
            for log in sensitive_logs
        ]


# ============================================================================
# Singleton Instance
# ============================================================================

_audit_logger_instance = None


def get_audit_logger() -> AuditLoggerService:
    """Get singleton audit logger instance"""

    global _audit_logger_instance
    if _audit_logger_instance is None:
        _audit_logger_instance = AuditLoggerService(DATABASE_URL)

    return _audit_logger_instance
