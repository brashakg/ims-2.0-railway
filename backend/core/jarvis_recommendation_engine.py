"""
IMS 2.0 - Intelligent Recommendation Engine for JARVIS
======================================================

Generates actionable business recommendations based on data analysis.
Uses scoring and impact assessment to prioritize recommendations.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Any, Optional
from enum import Enum
import json


class RecommendationCategory(Enum):
    """Categories of recommendations"""
    INVENTORY = "inventory"
    SALES = "sales"
    STAFFING = "staffing"
    MARKETING = "marketing"
    PRICING = "pricing"
    CUSTOMER_RETENTION = "customer_retention"
    OPERATIONS = "operations"
    COMPLIANCE = "compliance"
    FINANCIAL = "financial"
    TRAINING = "training"


class Priority(Enum):
    """Recommendation priority levels"""
    CRITICAL = 5
    HIGH = 4
    MEDIUM = 3
    LOW = 2
    INFO = 1


@dataclass
class RecommendationMetric:
    """Metric data for recommendation"""
    name: str
    current_value: float
    target_value: float
    unit: str
    trend: str  # "up", "down", "stable"


@dataclass
class Recommendation:
    """Enhanced recommendation with scoring and impact"""
    id: str
    category: RecommendationCategory
    priority: Priority
    title: str
    description: str
    rationale: str
    expected_impact: str
    impact_value: float  # Estimated financial impact
    confidence_score: float  # 0-1
    implementation_effort: str  # "easy", "medium", "hard"
    implementation_steps: List[str]
    affected_metrics: List[RecommendationMetric]
    dependencies: List[str]  # Other recommendations that should be done first
    owner: Optional[str]  # Responsible person/role
    estimated_time: str  # "1 day", "1 week", etc
    success_criteria: List[str]
    created_at: datetime = field(default_factory=datetime.now)
    approved_by: Optional[str] = None
    status: str = "pending"  # pending, approved, in_progress, completed


@dataclass
class ActionPlan:
    """Structured action plan from recommendations"""
    id: str
    title: str
    recommendations: List[Recommendation]
    priority_order: List[str]  # IDs in priority order
    total_estimated_impact: float
    total_effort_days: float
    timeline: Dict[str, List[str]]  # phase -> recommendation IDs
    success_metrics: List[str]
    risk_mitigation: List[str]


class JarvisRecommendationEngine:
    """Intelligent recommendation engine"""

    def __init__(self):
        self.recommendations: List[Recommendation] = []
        self.approved_recommendations: List[Recommendation] = []

    def generate_inventory_recommendations(self, inventory_data: Dict[str, Any]) -> List[Recommendation]:
        """Generate inventory management recommendations"""
        recommendations = []

        # Low stock analysis
        if "low_stock_items" in inventory_data:
            low_items = inventory_data["low_stock_items"]
            if low_items:
                recommendation = Recommendation(
                    id=f"inv_reorder_{int(datetime.now().timestamp())}",
                    category=RecommendationCategory.INVENTORY,
                    priority=Priority.CRITICAL,
                    title=f"Urgent: Reorder {len(low_items)} Critical Items",
                    description=f"{len(low_items)} high-demand products are at critical stock levels",
                    rationale="Critical items at risk of stockout causing lost sales",
                    expected_impact=f"Prevent potential ₹{self._calculate_stock_impact(low_items):.2f} in lost sales",
                    impact_value=self._calculate_stock_impact(low_items),
                    confidence_score=0.95,
                    implementation_effort="easy",
                    implementation_steps=[
                        "Review reorder quantities for each item",
                        "Check supplier lead times",
                        "Generate purchase orders",
                        "Confirm delivery schedule"
                    ],
                    affected_metrics=[
                        RecommendationMetric("Stock Levels", 2, 10, "units", "down"),
                        RecommendationMetric("Lost Sales Risk", 100, 0, "%", "up")
                    ],
                    dependencies=[],
                    owner="Inventory Manager",
                    estimated_time="2 hours",
                    success_criteria=["PO generated", "Delivery confirmed", "Stock levels > 10"]
                )
                recommendations.append(recommendation)

        # Overstock analysis
        if "overstock_items" in inventory_data:
            overstock = inventory_data["overstock_items"]
            if overstock:
                recommendation = Recommendation(
                    id=f"inv_clearance_{int(datetime.now().timestamp())}",
                    category=RecommendationCategory.INVENTORY,
                    priority=Priority.HIGH,
                    title=f"Clearance: {len(overstock)} Overstocked Items",
                    description=f"{len(overstock)} items are overstocked and tying up capital",
                    rationale="Excess inventory increases carrying costs and risk of obsolescence",
                    expected_impact=f"Free up ₹{self._calculate_overstock_impact(overstock):.2f} in working capital",
                    impact_value=self._calculate_overstock_impact(overstock),
                    confidence_score=0.85,
                    implementation_effort="medium",
                    implementation_steps=[
                        "Create clearance promotions",
                        "Offer bulk discounts",
                        "Bundle with popular items",
                        "Consider donations if necessary"
                    ],
                    affected_metrics=[
                        RecommendationMetric("Inventory Turnover", 4, 6, "months", "down"),
                        RecommendationMetric("Working Capital", 500000, 300000, "₹", "up")
                    ],
                    dependencies=[],
                    owner="Sales Manager",
                    estimated_time="1 week",
                    success_criteria=["50% of overstock cleared", "Clearance margin > 80%"]
                )
                recommendations.append(recommendation)

        return recommendations

    def generate_sales_recommendations(self, sales_data: Dict[str, Any]) -> List[Recommendation]:
        """Generate sales optimization recommendations"""
        recommendations = []

        if "slow_moving" in sales_data:
            slow_items = sales_data["slow_moving"]
            if slow_items:
                recommendation = Recommendation(
                    id=f"sales_promo_{int(datetime.now().timestamp())}",
                    category=RecommendationCategory.SALES,
                    priority=Priority.MEDIUM,
                    title=f"Launch Promotion for {len(slow_items)} Slow-Moving Products",
                    description="Boost sales of underperforming products through targeted promotions",
                    rationale="Increase product visibility and customer interest",
                    expected_impact=f"Increase sales by 20-30% for these products",
                    impact_value=self._calculate_promotion_impact(slow_items),
                    confidence_score=0.80,
                    implementation_effort="easy",
                    implementation_steps=[
                        "Analyze competitor pricing",
                        "Set promotional discount (15-25%)",
                        "Create marketing materials",
                        "Launch across all channels",
                        "Track performance daily"
                    ],
                    affected_metrics=[
                        RecommendationMetric("Product Velocity", 1, 3, "units/day", "down")
                    ],
                    dependencies=[],
                    owner="Marketing Manager",
                    estimated_time="3 days",
                    success_criteria=["Promotion launches on schedule", "20% sales increase"]
                )
                recommendations.append(recommendation)

        return recommendations

    def generate_staffing_recommendations(self, staffing_data: Dict[str, Any]) -> List[Recommendation]:
        """Generate staffing and HR recommendations"""
        recommendations = []

        if "understaffed_stores" in staffing_data:
            understaffed = staffing_data["understaffed_stores"]
            if understaffed:
                recommendation = Recommendation(
                    id=f"staff_transfer_{int(datetime.now().timestamp())}",
                    category=RecommendationCategory.STAFFING,
                    priority=Priority.HIGH,
                    title=f"Staff Rebalancing: Transfer to {len(understaffed)} Stores",
                    description="Rebalance staff allocation across stores for optimal coverage",
                    rationale="Understaffed stores have higher customer waiting times and lower sales",
                    expected_impact="Improve customer satisfaction and operational efficiency",
                    impact_value=25000,  # Estimated impact
                    confidence_score=0.90,
                    implementation_effort="medium",
                    implementation_steps=[
                        "Analyze staff capacity at each location",
                        "Identify transferable staff",
                        "Plan transfer schedule",
                        "Communicate with affected staff",
                        "Monitor impact"
                    ],
                    affected_metrics=[
                        RecommendationMetric("Staff per Store", 3, 5, "people", "down")
                    ],
                    dependencies=[],
                    owner="HR Manager",
                    estimated_time="1 week",
                    success_criteria=["All stores properly staffed", "Customer satisfaction > 4.5"]
                )
                recommendations.append(recommendation)

        return recommendations

    def generate_pricing_recommendations(self, pricing_data: Dict[str, Any]) -> List[Recommendation]:
        """Generate pricing optimization recommendations"""
        recommendations = []

        if "price_sensitive_items" in pricing_data:
            sensitive = pricing_data["price_sensitive_items"]
            if sensitive:
                recommendation = Recommendation(
                    id=f"pricing_optimize_{int(datetime.now().timestamp())}",
                    category=RecommendationCategory.PRICING,
                    priority=Priority.MEDIUM,
                    title="Optimize Pricing for Price-Sensitive Items",
                    description="Adjust prices on price-sensitive items to maximize margin",
                    rationale="Small price increases on price-sensitive items can significantly boost margin",
                    expected_impact="Increase margins by 5-8% while maintaining volume",
                    impact_value=45000,
                    confidence_score=0.75,
                    implementation_effort="medium",
                    implementation_steps=[
                        "Conduct price elasticity analysis",
                        "Test price points in pilot store",
                        "Monitor sales impact",
                        "Implement across all stores if positive",
                        "Review monthly"
                    ],
                    affected_metrics=[
                        RecommendationMetric("Margin %", 25, 30, "%", "down")
                    ],
                    dependencies=[],
                    owner="Finance Manager",
                    estimated_time="2 weeks",
                    success_criteria=["Margin increase > 5%", "Volume loss < 2%"]
                )
                recommendations.append(recommendation)

        return recommendations

    def create_action_plan(self, recommendations: List[Recommendation]) -> ActionPlan:
        """Create structured action plan from recommendations"""
        # Sort by priority and impact
        sorted_recs = sorted(
            recommendations,
            key=lambda r: (r.priority.value, r.impact_value),
            reverse=True
        )

        # Create phases
        timeline = {
            "immediate": [],  # This week
            "short_term": [],  # This month
            "medium_term": [],  # This quarter
            "long_term": []  # This year
        }

        for rec in sorted_recs[:5]:  # Top 5 recommendations
            if rec.priority in [Priority.CRITICAL, Priority.HIGH]:
                timeline["immediate"].append(rec.id)
            elif rec.estimated_time in ["1 day", "2 days", "3 days"]:
                timeline["short_term"].append(rec.id)
            elif rec.estimated_time in ["1 week", "2 weeks"]:
                timeline["medium_term"].append(rec.id)
            else:
                timeline["long_term"].append(rec.id)

        plan = ActionPlan(
            id=f"plan_{int(datetime.now().timestamp())}",
            title="AI-Generated Action Plan",
            recommendations=sorted_recs[:5],  # Top 5
            priority_order=[r.id for r in sorted_recs[:5]],
            total_estimated_impact=sum(r.impact_value for r in sorted_recs[:5]),
            total_effort_days=sum(self._parse_time_estimate(r.estimated_time) for r in sorted_recs[:5]),
            timeline=timeline,
            success_metrics=[
                "All critical items addressed",
                "75% of recommendations implemented",
                "Measurable business impact achieved"
            ],
            risk_mitigation=[
                "Regular monitoring and reporting",
                "Contingency plans for each recommendation",
                "Stakeholder communication"
            ]
        )

        return plan

    def _calculate_stock_impact(self, low_items: List) -> float:
        """Calculate financial impact of low stock"""
        return float(len(low_items) * 2500)  # Avg impact per item

    def _calculate_overstock_impact(self, overstock_items: List) -> float:
        """Calculate working capital tied up in overstock"""
        return float(len(overstock_items) * 3000)

    def _calculate_promotion_impact(self, slow_items: List) -> float:
        """Calculate potential impact from promotion"""
        return float(len(slow_items) * 1500)

    def _parse_time_estimate(self, time_str: str) -> float:
        """Parse time estimate to days"""
        if "day" in time_str:
            return float(time_str.split()[0])
        elif "week" in time_str:
            return float(time_str.split()[0]) * 7
        elif "hour" in time_str:
            return float(time_str.split()[0]) / 24
        return 1.0


# Initialize global recommendation engine
jarvis_recommender = JarvisRecommendationEngine()
