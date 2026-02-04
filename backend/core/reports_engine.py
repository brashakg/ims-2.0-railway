"""
IMS 2.0 - Reports Engine
=========================
Comprehensive reporting for all modules
"""
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from decimal import Decimal
from enum import Enum
from typing import List, Optional, Dict, Any
import uuid

class ReportType(Enum):
    # Sales
    SALES_SUMMARY = "SALES_SUMMARY"
    SALES_DETAILED = "SALES_DETAILED"
    SALES_BY_CATEGORY = "SALES_BY_CATEGORY"
    SALES_BY_STAFF = "SALES_BY_STAFF"
    SALES_BY_STORE = "SALES_BY_STORE"
    
    # Inventory
    STOCK_SUMMARY = "STOCK_SUMMARY"
    STOCK_MOVEMENT = "STOCK_MOVEMENT"
    LOW_STOCK = "LOW_STOCK"
    EXPIRY_REPORT = "EXPIRY_REPORT"
    STOCK_VALUATION = "STOCK_VALUATION"
    
    # Finance
    REVENUE_REPORT = "REVENUE_REPORT"
    COLLECTION_REPORT = "COLLECTION_REPORT"
    OUTSTANDING_REPORT = "OUTSTANDING_REPORT"
    GST_REPORT = "GST_REPORT"
    EXPENSE_REPORT = "EXPENSE_REPORT"
    PROFIT_LOSS = "PROFIT_LOSS"
    
    # HR
    ATTENDANCE_REPORT = "ATTENDANCE_REPORT"
    SALARY_REPORT = "SALARY_REPORT"
    INCENTIVE_REPORT = "INCENTIVE_REPORT"
    LEAVE_REPORT = "LEAVE_REPORT"
    
    # Clinical
    EYE_TEST_REPORT = "EYE_TEST_REPORT"
    PRESCRIPTION_REPORT = "PRESCRIPTION_REPORT"
    OPTOMETRIST_PERFORMANCE = "OPTOMETRIST_PERFORMANCE"
    
    # Customer
    CUSTOMER_REPORT = "CUSTOMER_REPORT"
    LOYALTY_REPORT = "LOYALTY_REPORT"

class ReportFormat(Enum):
    JSON = "JSON"
    CSV = "CSV"
    EXCEL = "EXCEL"
    PDF = "PDF"

@dataclass
class ReportRequest:
    id: str
    report_type: ReportType
    requested_by: str
    requested_at: datetime
    
    # Filters
    from_date: date
    to_date: date
    store_ids: List[str] = field(default_factory=list)
    category_codes: List[str] = field(default_factory=list)
    
    # Output
    format: ReportFormat = ReportFormat.JSON
    status: str = "PENDING"
    completed_at: Optional[datetime] = None
    result: Optional[Dict] = None
    file_path: Optional[str] = None

@dataclass
class ReportColumn:
    name: str
    label: str
    data_type: str = "string"
    aggregate: Optional[str] = None  # SUM, AVG, COUNT, etc.

class ReportsEngine:
    def __init__(self):
        self.requests: Dict[str, ReportRequest] = {}
        self._report_configs = self._init_report_configs()
    
    def _init_report_configs(self) -> Dict:
        return {
            ReportType.SALES_SUMMARY: {
                "name": "Sales Summary Report",
                "columns": [
                    ReportColumn("date", "Date", "date"),
                    ReportColumn("store", "Store", "string"),
                    ReportColumn("invoices", "Invoices", "number", "COUNT"),
                    ReportColumn("gross_amount", "Gross Amount", "currency", "SUM"),
                    ReportColumn("discount", "Discount", "currency", "SUM"),
                    ReportColumn("tax", "Tax", "currency", "SUM"),
                    ReportColumn("net_amount", "Net Amount", "currency", "SUM"),
                ]
            },
            ReportType.GST_REPORT: {
                "name": "GST Report",
                "columns": [
                    ReportColumn("invoice_no", "Invoice No"),
                    ReportColumn("invoice_date", "Date", "date"),
                    ReportColumn("customer_gstin", "Customer GSTIN"),
                    ReportColumn("taxable_value", "Taxable Value", "currency"),
                    ReportColumn("cgst", "CGST", "currency"),
                    ReportColumn("sgst", "SGST", "currency"),
                    ReportColumn("igst", "IGST", "currency"),
                    ReportColumn("total", "Total", "currency"),
                ]
            },
            ReportType.STOCK_VALUATION: {
                "name": "Stock Valuation Report",
                "columns": [
                    ReportColumn("store", "Store"),
                    ReportColumn("category", "Category"),
                    ReportColumn("quantity", "Qty", "number", "SUM"),
                    ReportColumn("cost_value", "Cost Value", "currency", "SUM"),
                    ReportColumn("retail_value", "Retail Value", "currency", "SUM"),
                ]
            },
            ReportType.ATTENDANCE_REPORT: {
                "name": "Attendance Report",
                "columns": [
                    ReportColumn("employee", "Employee"),
                    ReportColumn("store", "Store"),
                    ReportColumn("working_days", "Working Days", "number"),
                    ReportColumn("present", "Present", "number"),
                    ReportColumn("absent", "Absent", "number"),
                    ReportColumn("late", "Late", "number"),
                    ReportColumn("attendance_pct", "Attendance %", "percent"),
                ]
            }
        }
    
    def request_report(self, report_type: ReportType, requested_by: str,
                       from_date: date, to_date: date,
                       store_ids: List[str] = None,
                       format: ReportFormat = ReportFormat.JSON) -> ReportRequest:
        
        request = ReportRequest(
            id=str(uuid.uuid4()),
            report_type=report_type,
            requested_by=requested_by,
            requested_at=datetime.now(),
            from_date=from_date,
            to_date=to_date,
            store_ids=store_ids or [],
            format=format
        )
        
        self.requests[request.id] = request
        return request
    
    def generate_report(self, request_id: str, data_source: Dict = None) -> Dict:
        request = self.requests.get(request_id)
        if not request:
            return {"error": "Request not found"}
        
        request.status = "PROCESSING"
        
        # Generate based on type (using sample data)
        result = self._generate_sample_report(request, data_source or {})
        
        request.result = result
        request.status = "COMPLETED"
        request.completed_at = datetime.now()
        
        return result
    
    def _generate_sample_report(self, request: ReportRequest, data: Dict) -> Dict:
        config = self._report_configs.get(request.report_type, {})
        
        if request.report_type == ReportType.SALES_SUMMARY:
            return {
                "report_name": "Sales Summary Report",
                "period": f"{request.from_date} to {request.to_date}",
                "generated_at": datetime.now().isoformat(),
                "summary": {
                    "total_invoices": 156,
                    "gross_amount": 1250000,
                    "total_discount": 87500,
                    "total_tax": 209250,
                    "net_amount": 1371750
                },
                "by_store": [
                    {"store": "Bokaro", "invoices": 52, "amount": 450000},
                    {"store": "Ranchi", "invoices": 48, "amount": 420000},
                    {"store": "Dhanbad", "invoices": 56, "amount": 380000}
                ],
                "by_category": [
                    {"category": "Frame", "qty": 89, "amount": 620000},
                    {"category": "Lens", "qty": 156, "amount": 480000},
                    {"category": "Contact Lens", "qty": 45, "amount": 150000}
                ]
            }
        
        elif request.report_type == ReportType.GST_REPORT:
            return {
                "report_name": "GST Report",
                "period": f"{request.from_date} to {request.to_date}",
                "summary": {
                    "taxable_value": 1162500,
                    "cgst": 104625,
                    "sgst": 104625,
                    "igst": 0,
                    "total_tax": 209250
                },
                "b2b_invoices": 12,
                "b2c_invoices": 144
            }
        
        elif request.report_type == ReportType.ATTENDANCE_REPORT:
            return {
                "report_name": "Attendance Report",
                "period": f"{request.from_date} to {request.to_date}",
                "employees": [
                    {"name": "Neha Sharma", "store": "Bokaro", "present": 24, "absent": 2, "late": 3, "pct": 92.3},
                    {"name": "Rahul Singh", "store": "Bokaro", "present": 25, "absent": 1, "late": 1, "pct": 96.2},
                    {"name": "Priya Gupta", "store": "Ranchi", "present": 23, "absent": 3, "late": 2, "pct": 88.5}
                ]
            }
        
        elif request.report_type == ReportType.STOCK_VALUATION:
            return {
                "report_name": "Stock Valuation Report",
                "as_on": str(request.to_date),
                "total_cost_value": 8500000,
                "total_retail_value": 12750000,
                "by_category": [
                    {"category": "Frame", "qty": 2500, "cost": 4500000, "retail": 7500000},
                    {"category": "Lens", "qty": 1800, "cost": 2000000, "retail": 3000000},
                    {"category": "Contact Lens", "qty": 500, "cost": 800000, "retail": 1200000},
                    {"category": "Watch", "qty": 300, "cost": 1200000, "retail": 1050000}
                ]
            }
        
        return {"report_name": config.get("name", "Report"), "data": []}
    
    def get_available_reports(self, role: str) -> List[Dict]:
        """Get reports available for a role"""
        all_reports = [
            {"type": rt.value, "name": self._report_configs.get(rt, {}).get("name", rt.value)}
            for rt in ReportType
        ]
        
        # Filter by role (simplified)
        if role in ["SUPERADMIN", "ADMIN"]:
            return all_reports
        elif role in ["STORE_MANAGER", "AREA_MANAGER"]:
            exclude = ["PROFIT_LOSS", "SALARY_REPORT"]
            return [r for r in all_reports if r["type"] not in exclude]
        else:
            return [r for r in all_reports if r["type"] in ["SALES_SUMMARY", "STOCK_SUMMARY"]]
    
    def export_report(self, request_id: str, format: ReportFormat) -> str:
        """Export report to file (returns file path)"""
        request = self.requests.get(request_id)
        if not request or not request.result:
            return ""
        
        # In production, would generate actual file
        file_path = f"/reports/{request.report_type.value}_{request.id[:8]}.{format.value.lower()}"
        request.file_path = file_path
        return file_path


def demo_reports():
    print("=" * 60)
    print("IMS 2.0 REPORTS ENGINE DEMO")
    print("=" * 60)
    
    engine = ReportsEngine()
    
    # Sales Summary
    print("\n📊 Sales Summary Report")
    request = engine.request_report(
        ReportType.SALES_SUMMARY, "admin-001",
        date.today() - timedelta(days=30), date.today()
    )
    result = engine.generate_report(request.id)
    print(f"  Period: {result['period']}")
    print(f"  Total Invoices: {result['summary']['total_invoices']}")
    print(f"  Net Amount: ₹{result['summary']['net_amount']:,}")
    
    # GST Report
    print("\n📋 GST Report")
    request = engine.request_report(
        ReportType.GST_REPORT, "admin-001",
        date.today() - timedelta(days=30), date.today()
    )
    result = engine.generate_report(request.id)
    print(f"  Taxable Value: ₹{result['summary']['taxable_value']:,}")
    print(f"  Total Tax: ₹{result['summary']['total_tax']:,}")
    
    # Stock Valuation
    print("\n📦 Stock Valuation")
    request = engine.request_report(
        ReportType.STOCK_VALUATION, "admin-001",
        date.today(), date.today()
    )
    result = engine.generate_report(request.id)
    print(f"  Total Cost: ₹{result['total_cost_value']:,}")
    print(f"  Total Retail: ₹{result['total_retail_value']:,}")
    
    # Attendance
    print("\n👥 Attendance Report")
    request = engine.request_report(
        ReportType.ATTENDANCE_REPORT, "admin-001",
        date.today() - timedelta(days=30), date.today()
    )
    result = engine.generate_report(request.id)
    for emp in result['employees'][:3]:
        print(f"  {emp['name']}: {emp['pct']}% attendance")
    
    # Available Reports
    print("\n📑 Available Reports for Store Manager")
    reports = engine.get_available_reports("STORE_MANAGER")
    print(f"  {len(reports)} reports available")
    
    print("\n" + "=" * 60)


if __name__ == "__main__":
    demo_reports()
