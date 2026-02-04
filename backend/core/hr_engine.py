"""
IMS 2.0 - HR, Attendance & Payroll Engine
"""
from dataclasses import dataclass, field
from datetime import datetime, date, time, timedelta
from decimal import Decimal
from enum import Enum
from typing import List, Optional, Dict, Tuple
import uuid
import math

class EmployeeStatus(Enum):
    ACTIVE = "ACTIVE"
    ON_LEAVE = "ON_LEAVE"
    TERMINATED = "TERMINATED"

class Role(Enum):
    SALES_STAFF = "SALES_STAFF"
    SALES_CASHIER = "SALES_CASHIER"
    OPTOMETRIST = "OPTOMETRIST"
    WORKSHOP_STAFF = "WORKSHOP_STAFF"
    STORE_MANAGER = "STORE_MANAGER"
    AREA_MANAGER = "AREA_MANAGER"
    ACCOUNTANT = "ACCOUNTANT"
    CATALOG_MANAGER = "CATALOG_MANAGER"
    ADMIN = "ADMIN"
    SUPERADMIN = "SUPERADMIN"

class AttendanceStatus(Enum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    LATE = "LATE"
    ON_LEAVE = "ON_LEAVE"

class LeaveType(Enum):
    CASUAL = "CASUAL"
    SICK = "SICK"
    EARNED = "EARNED"

class LeaveStatus(Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

class SalaryComponent(Enum):
    BASIC = "BASIC"
    HRA = "HRA"
    CONVEYANCE = "CONVEYANCE"
    SPECIAL_ALLOWANCE = "SPECIAL_ALLOWANCE"
    INCENTIVE = "INCENTIVE"
    PF_DEDUCTION = "PF_DEDUCTION"
    ADVANCE_DEDUCTION = "ADVANCE_DEDUCTION"

@dataclass
class Shift:
    id: str
    name: str
    start_time: time
    end_time: time
    grace_minutes: int = 15

@dataclass
class Employee:
    id: str
    employee_code: str
    first_name: str
    last_name: str
    email: str
    phone: str
    roles: List[Role] = field(default_factory=list)
    primary_store_id: Optional[str] = None
    assigned_store_ids: List[str] = field(default_factory=list)
    shift_id: str = "shift-general"
    week_off_days: List[int] = field(default_factory=lambda: [0])
    salary_ctc: Decimal = Decimal("0")
    basic_salary: Decimal = Decimal("0")
    hra: Decimal = Decimal("0")
    conveyance: Decimal = Decimal("0")
    special_allowance: Decimal = Decimal("0")
    monthly_sales_target: Decimal = Decimal("0")
    incentive_percentage: Decimal = Decimal("0")
    casual_leave_balance: Decimal = Decimal("12")
    sick_leave_balance: Decimal = Decimal("6")
    advance_balance: Decimal = Decimal("0")
    status: EmployeeStatus = EmployeeStatus.ACTIVE
    
    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

@dataclass
class AttendanceRecord:
    id: str
    employee_id: str
    employee_name: str
    attendance_date: date
    store_id: str
    check_in_time: Optional[datetime] = None
    check_in_lat: Optional[float] = None
    check_in_lon: Optional[float] = None
    check_out_time: Optional[datetime] = None
    status: AttendanceStatus = AttendanceStatus.ABSENT
    late_minutes: int = 0
    worked_hours: Decimal = Decimal("0")

@dataclass
class LeaveRequest:
    id: str
    employee_id: str
    employee_name: str
    leave_type: LeaveType
    from_date: date
    to_date: date
    total_days: Decimal
    reason: str
    status: LeaveStatus = LeaveStatus.PENDING

@dataclass
class SalesAchievement:
    id: str
    employee_id: str
    employee_name: str
    month: int
    year: int
    target: Decimal = Decimal("0")
    achieved: Decimal = Decimal("0")
    percent: Decimal = Decimal("0")
    incentive: Decimal = Decimal("0")

@dataclass
class AdvanceRecord:
    id: str
    employee_id: str
    amount: Decimal
    balance: Decimal
    monthly_ded: Decimal
    status: str = "ACTIVE"

@dataclass
class PayslipItem:
    component: SalaryComponent
    description: str
    amount: Decimal
    is_deduction: bool = False

@dataclass
class Payslip:
    id: str
    employee_id: str
    employee_name: str
    month: int
    year: int
    items: List[PayslipItem] = field(default_factory=list)
    gross: Decimal = Decimal("0")
    deductions: Decimal = Decimal("0")
    net: Decimal = Decimal("0")

@dataclass
class StoreLocation:
    store_id: str
    lat: float
    lon: float
    radius: float = 100.0

class HREngine:
    def __init__(self):
        self.employees: Dict[str, Employee] = {}
        self.shifts: Dict[str, Shift] = {}
        self.attendance: Dict[str, AttendanceRecord] = {}
        self.leaves: Dict[str, LeaveRequest] = {}
        self.sales: Dict[str, SalesAchievement] = {}
        self.advances: Dict[str, AdvanceRecord] = {}
        self.payslips: Dict[str, Payslip] = {}
        self.locations: Dict[str, StoreLocation] = {}
        self._emp_counter = 0
        self._init_shifts()
        self.discount_caps = {
            Role.SALES_STAFF: Decimal("10"), Role.STORE_MANAGER: Decimal("20"),
            Role.AREA_MANAGER: Decimal("25"), Role.ADMIN: Decimal("100"), Role.SUPERADMIN: Decimal("100")
        }
    
    def _init_shifts(self):
        self.shifts["shift-general"] = Shift("shift-general", "General", time(10,0), time(19,0))
    
    def create_employee(self, first: str, last: str, email: str, phone: str, roles: List[Role], store_id: str, store_code: str, ctc: Decimal = Decimal("0")) -> Employee:
        self._emp_counter += 1
        emp = Employee(
            id=str(uuid.uuid4()), employee_code=f"EMP-{store_code}-{self._emp_counter:04d}",
            first_name=first, last_name=last, email=email, phone=phone,
            roles=roles, primary_store_id=store_id, assigned_store_ids=[store_id], salary_ctc=ctc
        )
        if ctc > 0:
            emp.basic_salary = (ctc * Decimal("0.40")).quantize(Decimal("1"))
            emp.hra = (ctc * Decimal("0.20")).quantize(Decimal("1"))
            emp.conveyance = Decimal("1600")
            emp.special_allowance = ctc - emp.basic_salary - emp.hra - emp.conveyance
        self.employees[emp.id] = emp
        return emp
    
    def get_discount_cap(self, emp_id: str) -> Decimal:
        emp = self.employees.get(emp_id)
        if not emp: return Decimal("0")
        return max((self.discount_caps.get(r, Decimal("0")) for r in emp.roles), default=Decimal("0"))
    
    def register_location(self, store_id: str, lat: float, lon: float, radius: float = 100.0):
        self.locations[store_id] = StoreLocation(store_id, lat, lon, radius)
    
    def _distance(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        R = 6371000
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dp, dl = math.radians(lat2-lat1), math.radians(lon2-lon1)
        a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    
    def check_in(self, emp_id: str, store_id: str, lat: float, lon: float) -> Tuple[bool, str]:
        emp = self.employees.get(emp_id)
        if not emp: return False, "Employee not found"
        if store_id not in emp.assigned_store_ids: return False, "Not assigned to store"
        
        loc = self.locations.get(store_id)
        if loc:
            dist = self._distance(lat, lon, loc.lat, loc.lon)
            if dist > loc.radius: return False, f"Too far ({dist:.0f}m)"
        
        key = f"{emp_id}:{date.today()}"
        if key in self.attendance: return False, "Already checked in"
        
        now = datetime.now()
        shift = self.shifts.get(emp.shift_id)
        late = 0
        status = AttendanceStatus.PRESENT
        if shift:
            sched = datetime.combine(date.today(), shift.start_time)
            grace = sched + timedelta(minutes=shift.grace_minutes)
            if now > grace:
                late = int((now - sched).total_seconds() / 60)
                status = AttendanceStatus.LATE
        
        self.attendance[key] = AttendanceRecord(
            id=str(uuid.uuid4()), employee_id=emp_id, employee_name=emp.full_name,
            attendance_date=date.today(), store_id=store_id,
            check_in_time=now, check_in_lat=lat, check_in_lon=lon,
            status=status, late_minutes=late
        )
        return True, f"Checked in at {now.strftime('%H:%M')}" + (f" (Late: {late}m)" if late else "")
    
    def check_out(self, emp_id: str) -> Tuple[bool, str]:
        key = f"{emp_id}:{date.today()}"
        rec = self.attendance.get(key)
        if not rec: return False, "No check-in"
        if rec.check_out_time: return False, "Already checked out"
        
        now = datetime.now()
        rec.check_out_time = now
        if rec.check_in_time:
            hrs = (now - rec.check_in_time).total_seconds() / 3600 - 1  # minus break
            rec.worked_hours = Decimal(str(max(0, hrs))).quantize(Decimal("0.01"))
        return True, f"Checked out. Worked: {rec.worked_hours}h"
    
    def apply_leave(self, emp_id: str, ltype: LeaveType, frm: date, to: date, reason: str) -> Tuple[bool, str]:
        emp = self.employees.get(emp_id)
        if not emp: return False, "Not found"
        days = Decimal(str((to - frm).days + 1))
        bal = {LeaveType.CASUAL: emp.casual_leave_balance, LeaveType.SICK: emp.sick_leave_balance}.get(ltype, Decimal("99"))
        if bal < days: return False, "Insufficient balance"
        
        req = LeaveRequest(str(uuid.uuid4()), emp_id, emp.full_name, ltype, frm, to, days, reason)
        self.leaves[req.id] = req
        return True, f"Leave for {days} days applied"
    
    def approve_leave(self, req_id: str) -> Tuple[bool, str]:
        req = self.leaves.get(req_id)
        if not req: return False, "Not found"
        emp = self.employees.get(req.employee_id)
        if emp:
            if req.leave_type == LeaveType.CASUAL: emp.casual_leave_balance -= req.total_days
            elif req.leave_type == LeaveType.SICK: emp.sick_leave_balance -= req.total_days
        req.status = LeaveStatus.APPROVED
        return True, "Approved"
    
    def record_sale(self, emp_id: str, amount: Decimal) -> Tuple[bool, str]:
        emp = self.employees.get(emp_id)
        if not emp: return False, "Not found"
        key = f"{emp_id}:{date.today().year}:{date.today().month}"
        if key not in self.sales:
            self.sales[key] = SalesAchievement(str(uuid.uuid4()), emp_id, emp.full_name, date.today().month, date.today().year, emp.monthly_sales_target)
        s = self.sales[key]
        s.achieved += amount
        s.percent = ((s.achieved / s.target * 100) if s.target > 0 else Decimal("0")).quantize(Decimal("0.01"))
        if s.achieved > s.target:
            s.incentive = ((s.achieved - s.target) * emp.incentive_percentage / 100).quantize(Decimal("0.01"))
        return True, f"Sale recorded. {s.percent}% achieved"
    
    def request_advance(self, emp_id: str, amount: Decimal, monthly: Decimal) -> Tuple[bool, str]:
        emp = self.employees.get(emp_id)
        if not emp: return False, "Not found"
        adv = AdvanceRecord(str(uuid.uuid4()), emp_id, amount, amount, monthly)
        emp.advance_balance += amount
        self.advances[adv.id] = adv
        return True, f"Advance ₹{amount} recorded"
    
    def generate_payslip(self, emp_id: str, month: int, year: int) -> Tuple[bool, str, Optional[Payslip]]:
        emp = self.employees.get(emp_id)
        if not emp: return False, "Not found", None
        
        ps = Payslip(str(uuid.uuid4()), emp_id, emp.full_name, month, year)
        ps.items.append(PayslipItem(SalaryComponent.BASIC, "Basic", emp.basic_salary))
        ps.items.append(PayslipItem(SalaryComponent.HRA, "HRA", emp.hra))
        ps.items.append(PayslipItem(SalaryComponent.CONVEYANCE, "Conveyance", emp.conveyance))
        ps.items.append(PayslipItem(SalaryComponent.SPECIAL_ALLOWANCE, "Special Allowance", emp.special_allowance))
        
        skey = f"{emp_id}:{year}:{month}"
        if skey in self.sales and self.sales[skey].incentive > 0:
            ps.items.append(PayslipItem(SalaryComponent.INCENTIVE, "Incentive", self.sales[skey].incentive))
        
        pf = (min(emp.basic_salary, Decimal("15000")) * Decimal("0.12")).quantize(Decimal("1"))
        ps.items.append(PayslipItem(SalaryComponent.PF_DEDUCTION, "PF", pf, True))
        
        for adv in self.advances.values():
            if adv.employee_id == emp_id and adv.status == "ACTIVE" and adv.balance > 0:
                ded = min(adv.balance, adv.monthly_ded)
                ps.items.append(PayslipItem(SalaryComponent.ADVANCE_DEDUCTION, "Advance", ded, True))
                adv.balance -= ded
                if adv.balance <= 0: adv.status = "CLEARED"
        
        for item in ps.items:
            if item.is_deduction: ps.deductions += item.amount
            else: ps.gross += item.amount
        ps.net = ps.gross - ps.deductions
        self.payslips[ps.id] = ps
        return True, f"Net: ₹{ps.net}", ps

def demo_hr():
    print("=" * 60)
    print("IMS 2.0 HR ENGINE DEMO")
    print("=" * 60)
    
    engine = HREngine()
    engine.register_location("store-001", 23.6693, 86.1511, 100)
    
    print("\n👤 Create Multi-Role Employee")
    emp = engine.create_employee("Neha", "Sharma", "neha@bv.in", "9876543210", 
                                  [Role.STORE_MANAGER, Role.OPTOMETRIST], "store-001", "BV", Decimal("600000"))
    print(f"  {emp.employee_code}: {emp.full_name}")
    print(f"  Roles: {[r.value for r in emp.roles]}")
    print(f"  Discount Cap: {engine.get_discount_cap(emp.id)}%")
    
    print("\n📍 Geo Attendance")
    ok, msg = engine.check_in(emp.id, "store-001", 23.6693, 86.1511)
    print(f"  Check-in: {msg}")
    ok, msg = engine.check_out(emp.id)
    print(f"  Check-out: {msg}")
    
    print("\n🏖️ Leave")
    ok, msg = engine.apply_leave(emp.id, LeaveType.CASUAL, date.today()+timedelta(7), date.today()+timedelta(9), "Wedding")
    print(f"  Apply: {msg}")
    
    print("\n💰 Sales Incentive")
    emp.monthly_sales_target = Decimal("500000")
    emp.incentive_percentage = Decimal("2")
    for _ in range(5): engine.record_sale(emp.id, Decimal("120000"))
    s = engine.sales.get(f"{emp.id}:{date.today().year}:{date.today().month}")
    if s: print(f"  Achieved: ₹{s.achieved} ({s.percent}%), Incentive: ₹{s.incentive}")
    
    print("\n💵 Advance")
    ok, msg = engine.request_advance(emp.id, Decimal("20000"), Decimal("5000"))
    print(f"  {msg}")
    
    print("\n📄 Payslip")
    ok, msg, ps = engine.generate_payslip(emp.id, date.today().month, date.today().year)
    print(f"  {msg}")
    print(f"  Gross: ₹{ps.gross}, Deductions: ₹{ps.deductions}")
    
    print("\n" + "=" * 60)

if __name__ == "__main__":
    demo_hr()
