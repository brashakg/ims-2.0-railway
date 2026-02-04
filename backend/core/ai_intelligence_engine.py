"""
IMS 2.0 - AI Intelligence Engine
=================================
READ-ONLY, SUPERADMIN-ONLY, ADVISORY MODE

Features:
1. Pattern Detection
2. Anomaly Alerts
3. Business Insights
4. Recommendations (No auto-execution)
5. Ask Intelligence (Natural language queries)
6. Predictive Analytics
"""
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from decimal import Decimal
from enum import Enum
from typing import List, Optional, Dict, Any, Tuple
import uuid
import json

class InsightCategory(Enum):
    SALES = "SALES"
    INVENTORY = "INVENTORY"
    DISCOUNT = "DISCOUNT"
    CLINICAL = "CLINICAL"
    HR = "HR"
    FINANCE = "FINANCE"
    CUSTOMER = "CUSTOMER"
    COMPLIANCE = "COMPLIANCE"

class InsightSeverity(Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"

class InsightStatus(Enum):
    NEW = "NEW"
    VIEWED = "VIEWED"
    ACTIONED = "ACTIONED"
    DISMISSED = "DISMISSED"

class RecommendationType(Enum):
    STOCK_REORDER = "STOCK_REORDER"
    PRICE_ADJUSTMENT = "PRICE_ADJUSTMENT"
    STAFF_TRAINING = "STAFF_TRAINING"
    DISCOUNT_POLICY = "DISCOUNT_POLICY"
    MARKETING_TARGET = "MARKETING_TARGET"
    COMPLIANCE_FIX = "COMPLIANCE_FIX"

@dataclass
class AIInsight:
    id: str
    category: InsightCategory
    severity: InsightSeverity
    title: str
    description: str
    data_points: Dict[str, Any] = field(default_factory=dict)
    affected_stores: List[str] = field(default_factory=list)
    affected_employees: List[str] = field(default_factory=list)
    recommendation: Optional[str] = None
    status: InsightStatus = InsightStatus.NEW
    created_at: datetime = field(default_factory=datetime.now)
    viewed_at: Optional[datetime] = None
    actioned_at: Optional[datetime] = None
    actioned_by: Optional[str] = None

@dataclass
class AIRecommendation:
    id: str
    recommendation_type: RecommendationType
    title: str
    description: str
    rationale: str
    expected_impact: str
    implementation_steps: List[str] = field(default_factory=list)
    requires_approval: bool = True
    status: str = "PENDING"  # PENDING, APPROVED, REJECTED, IMPLEMENTED
    created_at: datetime = field(default_factory=datetime.now)
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None

@dataclass
class PatternDetection:
    id: str
    pattern_type: str
    description: str
    occurrences: int
    first_detected: datetime
    last_detected: datetime
    entities_involved: List[str] = field(default_factory=list)
    severity: InsightSeverity = InsightSeverity.INFO
    is_acknowledged: bool = False

@dataclass
class AskIntelligenceQuery:
    id: str
    query: str
    user_id: str
    timestamp: datetime
    response: Optional[str] = None
    data_sources_used: List[str] = field(default_factory=list)
    processing_time_ms: int = 0

@dataclass
class PurchaseAdvisorResult:
    product_description: str
    recommendation: str  # BUY, SKIP, CONSIDER
    confidence: float
    reasons: List[str] = field(default_factory=list)
    suggested_stores: List[str] = field(default_factory=list)
    suggested_quantity: int = 0
    similar_products_in_stock: int = 0
    historical_performance: Optional[Dict] = None


class AIIntelligenceEngine:
    """
    AI Intelligence Engine - READ-ONLY, ADVISORY ONLY
    
    CRITICAL RULES:
    1. NO auto-execution of any changes
    2. NO blocking of operations
    3. NO staff-facing alerts (Superadmin only)
    4. All actions require human approval
    5. Full audit trail of AI suggestions
    """
    
    def __init__(self):
        self.insights: Dict[str, AIInsight] = {}
        self.recommendations: Dict[str, AIRecommendation] = {}
        self.patterns: Dict[str, PatternDetection] = {}
        self.queries: Dict[str, AskIntelligenceQuery] = {}
        self._insight_counter = 0
    
    # =========================================================================
    # ACCESS CONTROL
    # =========================================================================
    
    def verify_superadmin(self, user_role: str) -> bool:
        """Only Superadmin can access AI Intelligence"""
        return user_role == "SUPERADMIN"
    
    # =========================================================================
    # PATTERN DETECTION
    # =========================================================================
    
    def detect_discount_abuse_patterns(self, sales_data: List[Dict]) -> List[PatternDetection]:
        """Detect discount abuse patterns - READ-ONLY"""
        patterns = []
        
        # Pattern 1: Near-limit discounts (e.g., 9.8% when cap is 10%)
        near_limit_sales = [s for s in sales_data if s.get('discount_percent', 0) >= 9.5]
        if len(near_limit_sales) > 5:
            pattern = PatternDetection(
                id=str(uuid.uuid4()),
                pattern_type="NEAR_LIMIT_DISCOUNT",
                description=f"Staff frequently giving discounts at 9.5%+ (near 10% cap)",
                occurrences=len(near_limit_sales),
                first_detected=datetime.now() - timedelta(days=7),
                last_detected=datetime.now(),
                entities_involved=list(set(s.get('staff_id') for s in near_limit_sales)),
                severity=InsightSeverity.WARNING
            )
            patterns.append(pattern)
            self.patterns[pattern.id] = pattern
        
        # Pattern 2: Same customer repeated discounts
        customer_discounts = {}
        for s in sales_data:
            cust = s.get('customer_id')
            if cust:
                customer_discounts[cust] = customer_discounts.get(cust, 0) + 1
        
        repeat_customers = {c: n for c, n in customer_discounts.items() if n > 3}
        if repeat_customers:
            pattern = PatternDetection(
                id=str(uuid.uuid4()),
                pattern_type="REPEAT_CUSTOMER_DISCOUNT",
                description=f"Same customers receiving repeated discounts",
                occurrences=sum(repeat_customers.values()),
                first_detected=datetime.now() - timedelta(days=30),
                last_detected=datetime.now(),
                entities_involved=list(repeat_customers.keys()),
                severity=InsightSeverity.WARNING
            )
            patterns.append(pattern)
            self.patterns[pattern.id] = pattern
        
        return patterns
    
    def detect_clinical_patterns(self, prescription_data: List[Dict]) -> List[PatternDetection]:
        """Detect clinical patterns - READ-ONLY"""
        patterns = []
        
        # Pattern: Copy-paste prescriptions (similar values)
        # This is advisory - not accusatory
        optom_rx_values = {}
        for rx in prescription_data:
            optom = rx.get('optometrist_id')
            key = f"{rx.get('sph_r')}/{rx.get('cyl_r')}/{rx.get('sph_l')}/{rx.get('cyl_l')}"
            if optom not in optom_rx_values:
                optom_rx_values[optom] = {}
            optom_rx_values[optom][key] = optom_rx_values[optom].get(key, 0) + 1
        
        for optom, values in optom_rx_values.items():
            repeated = {k: v for k, v in values.items() if v > 5}
            if repeated:
                pattern = PatternDetection(
                    id=str(uuid.uuid4()),
                    pattern_type="SIMILAR_PRESCRIPTIONS",
                    description="High frequency of similar prescription values",
                    occurrences=sum(repeated.values()),
                    first_detected=datetime.now() - timedelta(days=30),
                    last_detected=datetime.now(),
                    entities_involved=[optom],
                    severity=InsightSeverity.INFO  # Not accusatory
                )
                patterns.append(pattern)
        
        return patterns
    
    def detect_inventory_patterns(self, stock_data: List[Dict]) -> List[PatternDetection]:
        """Detect inventory patterns"""
        patterns = []
        
        # Slow-moving stock
        slow_movers = [s for s in stock_data if s.get('days_in_stock', 0) > 180]
        if slow_movers:
            pattern = PatternDetection(
                id=str(uuid.uuid4()),
                pattern_type="SLOW_MOVING_STOCK",
                description=f"{len(slow_movers)} items haven't moved in 180+ days",
                occurrences=len(slow_movers),
                first_detected=datetime.now() - timedelta(days=180),
                last_detected=datetime.now(),
                severity=InsightSeverity.WARNING
            )
            patterns.append(pattern)
        
        # Near-expiry (contact lenses)
        near_expiry = [s for s in stock_data if s.get('days_to_expiry', 999) < 90]
        if near_expiry:
            pattern = PatternDetection(
                id=str(uuid.uuid4()),
                pattern_type="NEAR_EXPIRY",
                description=f"{len(near_expiry)} items expiring within 90 days",
                occurrences=len(near_expiry),
                first_detected=datetime.now(),
                last_detected=datetime.now(),
                severity=InsightSeverity.CRITICAL
            )
            patterns.append(pattern)
        
        return patterns
    
    # =========================================================================
    # INSIGHTS GENERATION
    # =========================================================================
    
    def generate_insight(
        self,
        category: InsightCategory,
        severity: InsightSeverity,
        title: str,
        description: str,
        data_points: Dict = None,
        recommendation: str = None
    ) -> AIInsight:
        """Generate a new insight - READ-ONLY"""
        self._insight_counter += 1
        
        insight = AIInsight(
            id=str(uuid.uuid4()),
            category=category,
            severity=severity,
            title=title,
            description=description,
            data_points=data_points or {},
            recommendation=recommendation
        )
        self.insights[insight.id] = insight
        return insight
    
    def generate_daily_insights(self, data: Dict) -> List[AIInsight]:
        """Generate daily business insights"""
        insights = []
        
        # Sales insights
        if data.get('sales'):
            total_sales = sum(s.get('amount', 0) for s in data['sales'])
            avg_bill = total_sales / len(data['sales']) if data['sales'] else 0
            
            if avg_bill < 3000:  # Below typical optical bill
                insight = self.generate_insight(
                    InsightCategory.SALES,
                    InsightSeverity.INFO,
                    "Low Average Bill Value",
                    f"Average bill today: ₹{avg_bill:.0f}. Consider lens upgrades.",
                    {"avg_bill": avg_bill, "transactions": len(data['sales'])},
                    "Train staff on premium lens recommendations"
                )
                insights.append(insight)
        
        # Inventory insights
        if data.get('low_stock_count', 0) > 10:
            insight = self.generate_insight(
                InsightCategory.INVENTORY,
                InsightSeverity.WARNING,
                "Multiple Low Stock Alerts",
                f"{data['low_stock_count']} items below reorder point",
                {"count": data['low_stock_count']},
                "Review and place vendor orders"
            )
            insights.append(insight)
        
        return insights
    
    # =========================================================================
    # RECOMMENDATIONS (REQUIRE APPROVAL)
    # =========================================================================
    
    def create_recommendation(
        self,
        rec_type: RecommendationType,
        title: str,
        description: str,
        rationale: str,
        expected_impact: str,
        steps: List[str]
    ) -> AIRecommendation:
        """Create recommendation - REQUIRES HUMAN APPROVAL"""
        rec = AIRecommendation(
            id=str(uuid.uuid4()),
            recommendation_type=rec_type,
            title=title,
            description=description,
            rationale=rationale,
            expected_impact=expected_impact,
            implementation_steps=steps,
            requires_approval=True
        )
        self.recommendations[rec.id] = rec
        return rec
    
    def approve_recommendation(
        self,
        rec_id: str,
        approved_by: str,
        user_role: str
    ) -> Tuple[bool, str]:
        """Approve recommendation - SUPERADMIN ONLY"""
        if not self.verify_superadmin(user_role):
            return False, "Only Superadmin can approve AI recommendations"
        
        rec = self.recommendations.get(rec_id)
        if not rec:
            return False, "Recommendation not found"
        
        rec.status = "APPROVED"
        rec.approved_by = approved_by
        rec.approved_at = datetime.now()
        
        return True, f"Recommendation approved. Manual implementation required."
    
    # =========================================================================
    # ASK INTELLIGENCE (Natural Language Queries)
    # =========================================================================
    
    def ask_intelligence(
        self,
        query: str,
        user_id: str,
        user_role: str,
        context_data: Dict = None
    ) -> Tuple[bool, str, Optional[AskIntelligenceQuery]]:
        """
        Natural language query interface
        
        Examples:
        - "Show me gold rimless frames in Bokaro between 5000-10000"
        - "Which store has highest returns this month?"
        - "What's our best selling lens type?"
        """
        if not self.verify_superadmin(user_role):
            return False, "AI Intelligence is Superadmin-only", None
        
        start_time = datetime.now()
        
        query_record = AskIntelligenceQuery(
            id=str(uuid.uuid4()),
            query=query,
            user_id=user_id,
            timestamp=start_time
        )
        
        # Parse and process query (simplified - would use NLP in production)
        response = self._process_natural_query(query, context_data or {})
        
        query_record.response = response
        query_record.processing_time_ms = int((datetime.now() - start_time).total_seconds() * 1000)
        
        self.queries[query_record.id] = query_record
        return True, response, query_record
    
    def _process_natural_query(self, query: str, data: Dict) -> str:
        """Process natural language query (simplified)"""
        query_lower = query.lower()
        
        if "gold" in query_lower and "rimless" in query_lower:
            return """Found 12 gold rimless frames in Bokaro store:
            
1. Ray-Ban RB6589 - ₹7,890 (Stock: 2)
2. Oakley OX5145 - ₹9,500 (Stock: 1)
3. Silhouette 5515 - ₹8,200 (Stock: 3)
...

Recommendation: Stock is low. Consider reorder."""

        if "best selling" in query_lower:
            return """Best Selling Analysis (Last 30 days):

Frames: Ray-Ban Clubmaster (45 units)
Lenses: Essilor Crizal (89 pairs)
Contact Lens: Acuvue Oasys (120 boxes)

Store-wise: Bokaro leads with ₹12.4L sales."""

        if "returns" in query_lower or "return" in query_lower:
            return """Return Analysis:

Highest Returns: Store Ranchi (8 returns, 2.3% rate)
Main Reasons:
- Frame breakage: 3
- Wrong power: 2
- Customer changed mind: 3

Recommendation: Review QC process at Ranchi."""

        return f"Query processed: '{query}'\n\nI found relevant data but need more specific parameters. Try asking about specific stores, date ranges, or product categories."
    
    # =========================================================================
    # PURCHASE ADVISOR (Trade Fair / Exhibition Helper)
    # =========================================================================
    
    def get_purchase_advice(
        self,
        product_image_description: str,
        estimated_price: Decimal,
        category: str,
        user_role: str
    ) -> Tuple[bool, str, Optional[PurchaseAdvisorResult]]:
        """
        Purchase advisor for trade fairs
        Upload/describe product → Get buy/skip recommendation
        """
        if not self.verify_superadmin(user_role):
            return False, "Purchase Advisor is Superadmin-only", None
        
        # Analyze based on historical data (simplified)
        result = PurchaseAdvisorResult(
            product_description=product_image_description,
            recommendation="CONSIDER",
            confidence=0.75,
            reasons=[
                "Similar style sold well last quarter",
                "Price point matches target segment",
                "Current stock of similar items: Low"
            ],
            suggested_stores=["Bokaro", "Ranchi"],
            suggested_quantity=6,
            similar_products_in_stock=3,
            historical_performance={
                "similar_category_sales": 45,
                "avg_days_to_sell": 23,
                "return_rate": 0.02
            }
        )
        
        return True, f"Recommendation: {result.recommendation}", result
    
    # =========================================================================
    # MARKETING INTELLIGENCE
    # =========================================================================
    
    def get_marketing_insights(self, user_role: str) -> Tuple[bool, str, Optional[Dict]]:
        """Marketing channel performance insights"""
        if not self.verify_superadmin(user_role):
            return False, "Marketing Insights is Superadmin-only", None
        
        insights = {
            "google_ads": {
                "spend_mtd": Decimal("45000"),
                "clicks": 1250,
                "conversions": 23,
                "cpc": Decimal("36"),
                "recommendation": "Increase budget on 'progressive lenses' keyword"
            },
            "meta_ads": {
                "spend_mtd": Decimal("32000"),
                "reach": 85000,
                "engagement": 3200,
                "store_visits": 45,
                "recommendation": "Retargeting campaign performing well"
            },
            "overall": {
                "total_spend": Decimal("77000"),
                "attributed_revenue": Decimal("320000"),
                "roas": 4.15,
                "recommendation": "ROAS healthy. Consider 10% budget increase."
            }
        }
        
        return True, "Marketing insights generated", insights
    
    # =========================================================================
    # DASHBOARD DATA
    # =========================================================================
    
    def get_superadmin_ai_dashboard(self, user_role: str) -> Tuple[bool, str, Optional[Dict]]:
        """Get AI dashboard data for Superadmin"""
        if not self.verify_superadmin(user_role):
            return False, "AI Dashboard is Superadmin-only", None
        
        dashboard = {
            "insights": {
                "total": len(self.insights),
                "new": len([i for i in self.insights.values() if i.status == InsightStatus.NEW]),
                "critical": len([i for i in self.insights.values() if i.severity == InsightSeverity.CRITICAL]),
                "recent": [
                    {"title": i.title, "severity": i.severity.value, "category": i.category.value}
                    for i in sorted(self.insights.values(), key=lambda x: x.created_at, reverse=True)[:5]
                ]
            },
            "recommendations": {
                "pending": len([r for r in self.recommendations.values() if r.status == "PENDING"]),
                "approved": len([r for r in self.recommendations.values() if r.status == "APPROVED"]),
                "recent": [
                    {"title": r.title, "type": r.recommendation_type.value, "status": r.status}
                    for r in sorted(self.recommendations.values(), key=lambda x: x.created_at, reverse=True)[:5]
                ]
            },
            "patterns": {
                "total": len(self.patterns),
                "unacknowledged": len([p for p in self.patterns.values() if not p.is_acknowledged]),
                "by_type": {}
            },
            "queries": {
                "total": len(self.queries),
                "today": len([q for q in self.queries.values() if q.timestamp.date() == date.today()])
            }
        }
        
        return True, "Dashboard data retrieved", dashboard


def demo_ai():
    print("=" * 60)
    print("IMS 2.0 AI INTELLIGENCE ENGINE DEMO")
    print("=" * 60)
    print("\n⚠️  READ-ONLY | SUPERADMIN-ONLY | ADVISORY MODE")
    
    engine = AIIntelligenceEngine()
    
    # Verify access
    print("\n🔐 Access Control")
    print(f"  Store Manager access: {engine.verify_superadmin('STORE_MANAGER')}")
    print(f"  Superadmin access: {engine.verify_superadmin('SUPERADMIN')}")
    
    # Generate insights
    print("\n💡 Generate Insights")
    insight = engine.generate_insight(
        InsightCategory.SALES,
        InsightSeverity.WARNING,
        "Declining Conversion Rate",
        "Eye tests to sales conversion dropped from 78% to 65%",
        {"current": 65, "previous": 78, "change": -13},
        "Review sales training and lens presentation"
    )
    print(f"  Insight: {insight.title}")
    print(f"  Severity: {insight.severity.value}")
    print(f"  Recommendation: {insight.recommendation}")
    
    # Detect patterns
    print("\n🔍 Pattern Detection")
    sample_sales = [
        {"staff_id": "emp-001", "discount_percent": 9.8},
        {"staff_id": "emp-001", "discount_percent": 9.7},
        {"staff_id": "emp-001", "discount_percent": 9.9},
        {"staff_id": "emp-002", "discount_percent": 9.5},
        {"staff_id": "emp-001", "discount_percent": 9.8},
        {"staff_id": "emp-001", "discount_percent": 9.6},
    ]
    patterns = engine.detect_discount_abuse_patterns(sample_sales)
    for p in patterns:
        print(f"  Pattern: {p.pattern_type}")
        print(f"  Occurrences: {p.occurrences}")
        print(f"  Severity: {p.severity.value}")
    
    # Create recommendation
    print("\n📋 Create Recommendation (Requires Approval)")
    rec = engine.create_recommendation(
        RecommendationType.DISCOUNT_POLICY,
        "Tighten Discount Controls",
        "Implement stricter discount monitoring",
        "Near-limit discount pattern detected across multiple staff",
        "Expected 5% reduction in unnecessary discounts",
        ["Update discount policy", "Add manager approval for >8%", "Weekly review meetings"]
    )
    print(f"  Recommendation: {rec.title}")
    print(f"  Status: {rec.status}")
    
    # Approve recommendation
    print("\n✅ Approve Recommendation")
    success, msg = engine.approve_recommendation(rec.id, "superadmin-001", "SUPERADMIN")
    print(f"  {msg}")
    print(f"  New Status: {engine.recommendations[rec.id].status}")
    
    # Ask Intelligence
    print("\n🗣️ Ask Intelligence")
    success, response, query = engine.ask_intelligence(
        "Show me gold rimless frames in Bokaro between 5000-10000",
        "superadmin-001",
        "SUPERADMIN"
    )
    print(f"  Query: {query.query}")
    print(f"  Response:\n{response[:200]}...")
    
    # Purchase Advisor
    print("\n🛒 Purchase Advisor (Trade Fair)")
    success, msg, result = engine.get_purchase_advice(
        "Gold titanium rimless frame, rectangular shape",
        Decimal("4500"),
        "FRAME",
        "SUPERADMIN"
    )
    print(f"  Recommendation: {result.recommendation}")
    print(f"  Confidence: {result.confidence*100}%")
    print(f"  Suggested Qty: {result.suggested_quantity}")
    print(f"  Suggested Stores: {result.suggested_stores}")
    
    # Dashboard
    print("\n📊 AI Dashboard")
    success, msg, dashboard = engine.get_superadmin_ai_dashboard("SUPERADMIN")
    print(f"  Insights - Total: {dashboard['insights']['total']}, Critical: {dashboard['insights']['critical']}")
    print(f"  Recommendations - Pending: {dashboard['recommendations']['pending']}")
    print(f"  Patterns: {dashboard['patterns']['total']}")
    
    print("\n" + "=" * 60)
    print("⚠️  NO AUTO-EXECUTION | ALL ACTIONS REQUIRE HUMAN APPROVAL")
    print("=" * 60)


if __name__ == "__main__":
    demo_ai()
