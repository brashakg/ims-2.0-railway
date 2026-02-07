"""
IMS 2.0 - Compliance & Risk Management Engine for JARVIS
========================================================

Monitors business operations for compliance violations and risk indicators.
Tracks regulatory requirements, policy adherence, and operational risks.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from enum import Enum
import json


class ComplianceArea(Enum):
    """Areas of business compliance"""
    FINANCIAL = "financial"
    CUSTOMER_DATA = "customer_data"
    INVENTORY = "inventory"
    HR = "hr"
    SALES = "sales"
    GST = "gst"
    LABOR = "labor"
    SAFETY = "safety"
    ENVIRONMENTAL = "environmental"
    OPERATIONAL = "operational"


class RiskLevel(Enum):
    """Risk severity levels"""
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


@dataclass
class ComplianceRule:
    """A compliance rule to enforce"""
    id: str
    area: ComplianceArea
    rule_name: str
    description: str
    requirement: str
    threshold: float
    check_frequency: str  # "daily", "weekly", "monthly"
    penalty: str  # Description of penalty if violated
    severity: RiskLevel
    auto_check: bool = True


@dataclass
class ComplianceViolation:
    """Detected compliance violation"""
    id: str
    rule_id: str
    violation_type: str
    severity: RiskLevel
    description: str
    evidence: Dict[str, Any]
    detected_at: datetime
    affected_entities: List[str]  # Store IDs, employee IDs, etc
    recommended_action: str
    deadline: datetime
    resolution_status: str = "pending"  # pending, in_progress, resolved
    resolved_at: Optional[datetime] = None


@dataclass
class RiskIndicator:
    """Risk indicator detected in operations"""
    id: str
    risk_type: str
    risk_level: RiskLevel
    description: str
    probability: float  # 0-1
    potential_impact: str
    detected_at: datetime
    mitigation_actions: List[str]
    owner: Optional[str]
    monitoring: bool = True


@dataclass
class AuditTrail:
    """Audit trail entry for compliance tracking"""
    id: str
    timestamp: datetime
    action: str
    user_id: str
    entity_type: str
    entity_id: str
    changes: Dict[str, Tuple[Any, Any]]  # (old_value, new_value)
    ip_address: str
    status: str  # success, failure, pending
    notes: Optional[str] = None


@dataclass
class ComplianceReport:
    """Comprehensive compliance report"""
    report_id: str
    period: str  # "monthly", "quarterly", "yearly"
    generated_at: datetime
    total_violations: int
    violations_by_area: Dict[str, int]
    violations_by_severity: Dict[str, int]
    resolved_violations: int
    pending_violations: int
    active_risk_indicators: int
    compliance_score: float  # 0-100
    audit_entries_count: int
    recommendations: List[str]
    next_audit_date: datetime


class JarvisComplianceEngine:
    """Compliance and risk management engine"""

    def __init__(self):
        self.compliance_rules: List[ComplianceRule] = []
        self.violations: List[ComplianceViolation] = []
        self.risk_indicators: List[RiskIndicator] = []
        self.audit_trail: List[AuditTrail] = []
        self._initialize_default_rules()

    def _initialize_default_rules(self):
        """Initialize default compliance rules"""
        self.compliance_rules = [
            ComplianceRule(
                id="rule_gst_001",
                area=ComplianceArea.GST,
                rule_name="GST Filing Deadline",
                description="GST returns must be filed by 10th of next month",
                requirement="All GST returns filed on time",
                threshold=100.0,
                check_frequency="monthly",
                penalty="Late fees of ₹100-500 per day",
                severity=RiskLevel.HIGH,
            ),
            ComplianceRule(
                id="rule_inv_001",
                area=ComplianceArea.INVENTORY,
                rule_name="Stock Audit Frequency",
                description="Physical stock audit must be done quarterly",
                requirement="Audit completed within 90 days",
                threshold=90.0,
                check_frequency="quarterly",
                penalty="Inventory discrepancies undetected, financial impact",
                severity=RiskLevel.HIGH,
            ),
            ComplianceRule(
                id="rule_cust_001",
                area=ComplianceArea.CUSTOMER_DATA,
                rule_name="Data Protection",
                description="Customer data must be encrypted and backed up",
                requirement="Daily encrypted backups",
                threshold=100.0,
                check_frequency="daily",
                penalty="Data breach liability, ₹50000+ fines",
                severity=RiskLevel.CRITICAL,
            ),
            ComplianceRule(
                id="rule_fin_001",
                area=ComplianceArea.FINANCIAL,
                rule_name="Cash Reconciliation",
                description="Daily cash reconciliation and deposit",
                requirement="100% of daily cash reconciled",
                threshold=100.0,
                check_frequency="daily",
                penalty="Cash shortage, embezzlement risk",
                severity=RiskLevel.HIGH,
            ),
            ComplianceRule(
                id="rule_sal_001",
                area=ComplianceArea.SALES,
                rule_name="Invoice Documentation",
                description="All sales must have proper invoices",
                requirement="100% of sales documented",
                threshold=100.0,
                check_frequency="daily",
                penalty="Tax evasion charges, penalties",
                severity=RiskLevel.HIGH,
            ),
        ]

    def check_compliance(self, area: ComplianceArea, metric_value: float,
                        context: Dict[str, Any]) -> List[ComplianceViolation]:
        """Check compliance for a specific area"""
        violations = []

        for rule in self.compliance_rules:
            if rule.area == area and rule.auto_check:
                if metric_value < rule.threshold:
                    violation = ComplianceViolation(
                        id=f"violation_{int(datetime.now().timestamp())}",
                        rule_id=rule.id,
                        violation_type=rule.rule_name,
                        severity=rule.severity,
                        description=f"{rule.description}: Current value {metric_value}%, Required: {rule.threshold}%",
                        evidence=context,
                        detected_at=datetime.now(),
                        affected_entities=context.get("affected_stores", []),
                        recommended_action=f"Immediately address {rule.rule_name} to avoid: {rule.penalty}",
                        deadline=self._calculate_deadline(rule),
                    )

                    self.violations.append(violation)
                    violations.append(violation)

        return violations

    def detect_risk_indicators(self, operational_data: Dict[str, Any]) -> List[RiskIndicator]:
        """Detect potential risk indicators"""
        risks = []

        # Check for unusual transactions
        if "daily_transactions" in operational_data:
            transactions = operational_data["daily_transactions"]
            if transactions > 1000:  # Unusually high
                risk = RiskIndicator(
                    id=f"risk_{int(datetime.now().timestamp())}_high_trans",
                    risk_type="High Transaction Volume",
                    risk_level=RiskLevel.MEDIUM,
                    description=f"Unusual high transaction volume: {transactions} transactions",
                    probability=0.6,
                    potential_impact="Could indicate fraud or system issue",
                    detected_at=datetime.now(),
                    mitigation_actions=["Review transaction logs", "Verify payment reconciliation"],
                    owner="Finance Manager",
                )
                risks.append(risk)

        # Check for inventory discrepancies
        if "inventory_variance" in operational_data:
            variance = operational_data["inventory_variance"]
            if variance > 5:  # 5% variance is significant
                risk = RiskIndicator(
                    id=f"risk_{int(datetime.now().timestamp())}_inv_var",
                    risk_type="Inventory Variance",
                    risk_level=RiskLevel.HIGH,
                    description=f"High inventory variance: {variance}%",
                    probability=0.8,
                    potential_impact="Stock shortage, theft risk, financial loss",
                    detected_at=datetime.now(),
                    mitigation_actions=["Conduct physical audit", "Review warehouse procedures"],
                    owner="Inventory Manager",
                )
                risks.append(risk)

        # Check for cash shortages
        if "cash_variance" in operational_data:
            cash_var = operational_data["cash_variance"]
            if cash_var != 0:
                risk = RiskIndicator(
                    id=f"risk_{int(datetime.now().timestamp())}_cash_var",
                    risk_type="Cash Discrepancy",
                    risk_level=RiskLevel.CRITICAL if abs(cash_var) > 5000 else RiskLevel.HIGH,
                    description=f"Cash discrepancy detected: ₹{cash_var}",
                    probability=0.9,
                    potential_impact="Financial loss, embezzlement indication",
                    detected_at=datetime.now(),
                    mitigation_actions=["Recount cash", "Review transaction records", "Investigate discrepancy"],
                    owner="Finance Manager",
                )
                risks.append(risk)

        # Check for late GST filing
        if "days_since_gst_filing" in operational_data:
            days = operational_data["days_since_gst_filing"]
            if days > 10:
                risk = RiskIndicator(
                    id=f"risk_{int(datetime.now().timestamp())}_gst_late",
                    risk_type="Late GST Filing",
                    risk_level=RiskLevel.HIGH,
                    description=f"GST filing overdue by {days} days",
                    probability=1.0,
                    potential_impact="Penalty of ₹100-500 per day",
                    detected_at=datetime.now(),
                    mitigation_actions=["File GST immediately", "Pay applicable penalties"],
                    owner="Finance Manager",
                )
                risks.append(risk)

        self.risk_indicators.extend(risks)
        return risks

    def log_audit_trail(self, action: str, user_id: str, entity_type: str,
                       entity_id: str, changes: Dict[str, Tuple[Any, Any]],
                       ip_address: str, status: str = "success") -> AuditTrail:
        """Log an action to audit trail"""
        entry = AuditTrail(
            id=f"audit_{int(datetime.now().timestamp())}",
            timestamp=datetime.now(),
            action=action,
            user_id=user_id,
            entity_type=entity_type,
            entity_id=entity_id,
            changes=changes,
            ip_address=ip_address,
            status=status,
        )

        self.audit_trail.append(entry)
        return entry

    def resolve_violation(self, violation_id: str, resolution_notes: str) -> Optional[ComplianceViolation]:
        """Mark a violation as resolved"""
        for violation in self.violations:
            if violation.id == violation_id:
                violation.resolution_status = "resolved"
                violation.resolved_at = datetime.now()
                return violation

        return None

    def generate_compliance_report(self, period: str) -> ComplianceReport:
        """Generate comprehensive compliance report"""
        violations_by_area = {}
        violations_by_severity = {"low": 0, "medium": 0, "high": 0, "critical": 0}

        for violation in self.violations:
            # Count by area
            area_name = violation.rule_id.split("_")[1]
            violations_by_area[area_name] = violations_by_area.get(area_name, 0) + 1

            # Count by severity
            severity_name = violation.severity.name.lower()
            violations_by_severity[severity_name] = violations_by_severity.get(severity_name, 0) + 1

        resolved = sum(1 for v in self.violations if v.resolution_status == "resolved")
        pending = sum(1 for v in self.violations if v.resolution_status in ["pending", "in_progress"])

        compliance_score = self._calculate_compliance_score(resolved, pending, len(self.violations))

        recommendations = self._generate_compliance_recommendations(self.violations, self.risk_indicators)

        next_audit = datetime.now() + timedelta(days=30)

        report = ComplianceReport(
            report_id=f"compliance_report_{int(datetime.now().timestamp())}",
            period=period,
            generated_at=datetime.now(),
            total_violations=len(self.violations),
            violations_by_area=violations_by_area,
            violations_by_severity=violations_by_severity,
            resolved_violations=resolved,
            pending_violations=pending,
            active_risk_indicators=len(self.risk_indicators),
            compliance_score=compliance_score,
            audit_entries_count=len(self.audit_trail),
            recommendations=recommendations,
            next_audit_date=next_audit,
        )

        return report

    def _calculate_deadline(self, rule: ComplianceRule) -> datetime:
        """Calculate deadline for compliance"""
        if rule.check_frequency == "daily":
            return datetime.now() + timedelta(days=1)
        elif rule.check_frequency == "weekly":
            return datetime.now() + timedelta(days=7)
        elif rule.check_frequency == "monthly":
            return datetime.now() + timedelta(days=30)
        elif rule.check_frequency == "quarterly":
            return datetime.now() + timedelta(days=90)
        return datetime.now() + timedelta(days=30)

    def _calculate_compliance_score(self, resolved: int, pending: int, total: int) -> float:
        """Calculate overall compliance score"""
        if total == 0:
            return 100.0

        compliance_percentage = (resolved / total) * 100
        return max(0, min(100, compliance_percentage))

    def _generate_compliance_recommendations(self, violations: List[ComplianceViolation],
                                             risks: List[RiskIndicator]) -> List[str]:
        """Generate recommendations from violations and risks"""
        recommendations = []

        # Check critical violations
        critical = [v for v in violations if v.severity == RiskLevel.CRITICAL]
        if critical:
            recommendations.append(f"URGENT: Address {len(critical)} critical compliance violations immediately")

        # Check high-risk indicators
        high_risks = [r for r in risks if r.risk_level == RiskLevel.CRITICAL]
        if high_risks:
            recommendations.append(f"CRITICAL RISK: {len(high_risks)} critical risks detected - immediate action required")

        # Add specific recommendations
        for risk in risks:
            if risk.risk_level == RiskLevel.CRITICAL:
                recommendations.append(f"Priority: {risk.description}")

        return recommendations[:5]  # Top 5 recommendations


# Initialize global compliance engine
jarvis_compliance = JarvisComplianceEngine()
