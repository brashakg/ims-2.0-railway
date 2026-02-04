"""
IMS 2.0 - Expense & Advance Engine
===================================
Employee expenses, bill uploads, approvals, salary advances

Features:
1. Expense Categories (Travel, Food, Courier, Repairs, etc.)
2. Bill Upload with Image
3. Approval Hierarchy
4. Advance Against Salary
5. Settlement Tracking
6. Audit Trail
"""
from dataclasses import dataclass, field
from datetime import datetime, date
from decimal import Decimal
from enum import Enum
from typing import List, Optional, Dict, Tuple
import uuid
import hashlib

class ExpenseCategory(Enum):
    TRAVEL = "TRAVEL"
    FOOD = "FOOD"
    COURIER = "COURIER"
    REPAIRS = "REPAIRS"
    OFFICE_SUPPLIES = "OFFICE_SUPPLIES"
    CLIENT_RELATED = "CLIENT_RELATED"
    PETTY_CASH = "PETTY_CASH"
    UTILITIES = "UTILITIES"
    OTHER = "OTHER"

class ExpenseStatus(Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PAID = "PAID"
    CANCELLED = "CANCELLED"

class AdvanceType(Enum):
    SALARY_ADVANCE = "SALARY_ADVANCE"
    TRAVEL_ADVANCE = "TRAVEL_ADVANCE"
    SPECIAL_ADVANCE = "SPECIAL_ADVANCE"

class AdvanceStatus(Enum):
    REQUESTED = "REQUESTED"
    APPROVED = "APPROVED"
    DISBURSED = "DISBURSED"
    PARTIALLY_SETTLED = "PARTIALLY_SETTLED"
    FULLY_SETTLED = "FULLY_SETTLED"
    REJECTED = "REJECTED"

@dataclass
class BillUpload:
    id: str
    expense_id: str
    file_name: str
    file_path: str
    file_hash: str  # For duplicate detection
    file_size: int
    mime_type: str
    uploaded_at: datetime = field(default_factory=datetime.now)
    uploaded_by: str = ""

@dataclass
class Expense:
    id: str
    expense_number: str
    employee_id: str
    employee_name: str
    store_id: str
    
    # Details
    category: ExpenseCategory
    amount: Decimal
    description: str
    expense_date: date
    
    # Bill
    bill_uploads: List[BillUpload] = field(default_factory=list)
    has_bill: bool = False
    bill_waived: bool = False
    bill_waiver_reason: str = ""
    
    # Approval
    status: ExpenseStatus = ExpenseStatus.DRAFT
    submitted_at: Optional[datetime] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    rejection_reason: str = ""
    
    # Payment
    paid_at: Optional[datetime] = None
    payment_reference: str = ""
    
    # Linked advance (if settling)
    advance_id: Optional[str] = None
    
    # Audit
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: Optional[datetime] = None
    audit_trail: List[Dict] = field(default_factory=list)

@dataclass
class Advance:
    id: str
    advance_number: str
    employee_id: str
    employee_name: str
    store_id: str
    
    # Details
    advance_type: AdvanceType
    amount: Decimal
    purpose: str
    requested_date: date
    expected_settlement_date: Optional[date] = None
    
    # Status
    status: AdvanceStatus = AdvanceStatus.REQUESTED
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    disbursed_at: Optional[datetime] = None
    disbursement_reference: str = ""
    
    # Settlement
    settled_amount: Decimal = Decimal("0")
    settlement_expenses: List[str] = field(default_factory=list)  # Expense IDs
    
    # Deduction
    deduct_from_salary: bool = False
    deduction_months: int = 1  # Spread over N months
    
    # Audit
    created_at: datetime = field(default_factory=datetime.now)
    audit_trail: List[Dict] = field(default_factory=list)

@dataclass
class ExpensePolicy:
    category: ExpenseCategory
    daily_limit: Decimal
    monthly_limit: Decimal
    requires_bill: bool = True
    bill_waiver_allowed: bool = False
    approval_roles: List[str] = field(default_factory=list)


class ExpenseEngine:
    """
    Expense & Advance Management
    """
    
    def __init__(self):
        self.expenses: Dict[str, Expense] = {}
        self.advances: Dict[str, Advance] = {}
        self.bill_uploads: Dict[str, BillUpload] = {}
        self.policies: Dict[ExpenseCategory, ExpensePolicy] = {}
        self._exp_counter = 0
        self._adv_counter = 0
        self._bill_hashes: set = set()  # For duplicate detection
        
        self._initialize_policies()
    
    def _initialize_policies(self):
        """Initialize default expense policies"""
        self.policies = {
            ExpenseCategory.TRAVEL: ExpensePolicy(
                ExpenseCategory.TRAVEL, Decimal("2000"), Decimal("20000"),
                requires_bill=True, approval_roles=["STORE_MANAGER", "AREA_MANAGER", "ADMIN"]
            ),
            ExpenseCategory.FOOD: ExpensePolicy(
                ExpenseCategory.FOOD, Decimal("500"), Decimal("5000"),
                requires_bill=True, bill_waiver_allowed=True, 
                approval_roles=["STORE_MANAGER", "AREA_MANAGER"]
            ),
            ExpenseCategory.COURIER: ExpensePolicy(
                ExpenseCategory.COURIER, Decimal("1000"), Decimal("10000"),
                requires_bill=True, approval_roles=["STORE_MANAGER"]
            ),
            ExpenseCategory.REPAIRS: ExpensePolicy(
                ExpenseCategory.REPAIRS, Decimal("5000"), Decimal("25000"),
                requires_bill=True, approval_roles=["STORE_MANAGER", "ADMIN"]
            ),
            ExpenseCategory.OFFICE_SUPPLIES: ExpensePolicy(
                ExpenseCategory.OFFICE_SUPPLIES, Decimal("2000"), Decimal("10000"),
                requires_bill=True, approval_roles=["STORE_MANAGER"]
            ),
            ExpenseCategory.CLIENT_RELATED: ExpensePolicy(
                ExpenseCategory.CLIENT_RELATED, Decimal("3000"), Decimal("15000"),
                requires_bill=True, approval_roles=["STORE_MANAGER", "AREA_MANAGER"]
            ),
            ExpenseCategory.PETTY_CASH: ExpensePolicy(
                ExpenseCategory.PETTY_CASH, Decimal("500"), Decimal("5000"),
                requires_bill=False, approval_roles=["STORE_MANAGER"]
            ),
            ExpenseCategory.OTHER: ExpensePolicy(
                ExpenseCategory.OTHER, Decimal("1000"), Decimal("10000"),
                requires_bill=True, approval_roles=["STORE_MANAGER", "ADMIN"]
            ),
        }
    
    def _gen_expense_number(self) -> str:
        self._exp_counter += 1
        return f"EXP-{date.today().strftime('%Y%m')}-{self._exp_counter:05d}"
    
    def _gen_advance_number(self) -> str:
        self._adv_counter += 1
        return f"ADV-{date.today().strftime('%Y%m')}-{self._adv_counter:05d}"
    
    def _add_audit(self, record, action: str, by: str, details: str = ""):
        record.audit_trail.append({
            "action": action,
            "by": by,
            "timestamp": datetime.now().isoformat(),
            "details": details
        })
    
    # =========================================================================
    # EXPENSE MANAGEMENT
    # =========================================================================
    
    def create_expense(
        self,
        employee_id: str,
        employee_name: str,
        store_id: str,
        category: ExpenseCategory,
        amount: Decimal,
        description: str,
        expense_date: date
    ) -> Tuple[bool, str, Optional[Expense]]:
        """Create new expense entry"""
        
        # Check policy limits
        policy = self.policies.get(category)
        if policy and amount > policy.daily_limit:
            return False, f"Amount exceeds daily limit of ₹{policy.daily_limit}", None
        
        expense = Expense(
            id=str(uuid.uuid4()),
            expense_number=self._gen_expense_number(),
            employee_id=employee_id,
            employee_name=employee_name,
            store_id=store_id,
            category=category,
            amount=amount,
            description=description,
            expense_date=expense_date
        )
        
        self._add_audit(expense, "CREATED", employee_id, f"Amount: ₹{amount}")
        self.expenses[expense.id] = expense
        
        return True, f"Expense {expense.expense_number} created", expense
    
    def upload_bill(
        self,
        expense_id: str,
        file_name: str,
        file_path: str,
        file_content: bytes,
        mime_type: str,
        uploaded_by: str
    ) -> Tuple[bool, str]:
        """Upload bill for expense"""
        
        expense = self.expenses.get(expense_id)
        if not expense:
            return False, "Expense not found"
        
        if expense.status not in [ExpenseStatus.DRAFT, ExpenseStatus.SUBMITTED]:
            return False, "Cannot upload bill for this expense status"
        
        # Calculate hash for duplicate detection
        file_hash = hashlib.md5(file_content).hexdigest()
        
        if file_hash in self._bill_hashes:
            return False, "Duplicate bill detected - this bill already exists"
        
        bill = BillUpload(
            id=str(uuid.uuid4()),
            expense_id=expense_id,
            file_name=file_name,
            file_path=file_path,
            file_hash=file_hash,
            file_size=len(file_content),
            mime_type=mime_type,
            uploaded_by=uploaded_by
        )
        
        expense.bill_uploads.append(bill)
        expense.has_bill = True
        self._bill_hashes.add(file_hash)
        self.bill_uploads[bill.id] = bill
        
        self._add_audit(expense, "BILL_UPLOADED", uploaded_by, file_name)
        
        return True, "Bill uploaded successfully"
    
    def waive_bill(self, expense_id: str, reason: str, waived_by: str, waiver_role: str) -> Tuple[bool, str]:
        """Waive bill requirement"""
        expense = self.expenses.get(expense_id)
        if not expense:
            return False, "Expense not found"
        
        policy = self.policies.get(expense.category)
        if policy and not policy.bill_waiver_allowed:
            return False, f"Bill waiver not allowed for {expense.category.value}"
        
        if waiver_role not in ["ADMIN", "SUPERADMIN"]:
            return False, "Only Admin/Superadmin can waive bills"
        
        expense.bill_waived = True
        expense.bill_waiver_reason = reason
        self._add_audit(expense, "BILL_WAIVED", waived_by, reason)
        
        return True, "Bill requirement waived"
    
    def submit_expense(self, expense_id: str) -> Tuple[bool, str]:
        """Submit expense for approval"""
        expense = self.expenses.get(expense_id)
        if not expense:
            return False, "Expense not found"
        
        if expense.status != ExpenseStatus.DRAFT:
            return False, "Only draft expenses can be submitted"
        
        policy = self.policies.get(expense.category)
        if policy and policy.requires_bill and not expense.has_bill and not expense.bill_waived:
            return False, "Bill is required for this category"
        
        expense.status = ExpenseStatus.SUBMITTED
        expense.submitted_at = datetime.now()
        self._add_audit(expense, "SUBMITTED", expense.employee_id)
        
        return True, f"Expense {expense.expense_number} submitted for approval"
    
    def approve_expense(self, expense_id: str, approved_by: str, approver_role: str) -> Tuple[bool, str]:
        """Approve expense"""
        expense = self.expenses.get(expense_id)
        if not expense:
            return False, "Expense not found"
        
        if expense.status != ExpenseStatus.SUBMITTED:
            return False, "Only submitted expenses can be approved"
        
        # Check if approver has authority
        policy = self.policies.get(expense.category)
        if policy and approver_role not in policy.approval_roles and approver_role not in ["ADMIN", "SUPERADMIN"]:
            return False, f"Role {approver_role} cannot approve {expense.category.value} expenses"
        
        # Cannot approve own expense
        if expense.employee_id == approved_by:
            return False, "Cannot approve own expense"
        
        expense.status = ExpenseStatus.APPROVED
        expense.approved_by = approved_by
        expense.approved_at = datetime.now()
        self._add_audit(expense, "APPROVED", approved_by)
        
        return True, f"Expense {expense.expense_number} approved"
    
    def reject_expense(self, expense_id: str, rejected_by: str, reason: str) -> Tuple[bool, str]:
        """Reject expense"""
        expense = self.expenses.get(expense_id)
        if not expense:
            return False, "Expense not found"
        
        expense.status = ExpenseStatus.REJECTED
        expense.rejection_reason = reason
        self._add_audit(expense, "REJECTED", rejected_by, reason)
        
        return True, f"Expense {expense.expense_number} rejected"
    
    def mark_expense_paid(self, expense_id: str, payment_reference: str, paid_by: str) -> Tuple[bool, str]:
        """Mark expense as paid"""
        expense = self.expenses.get(expense_id)
        if not expense:
            return False, "Expense not found"
        
        if expense.status != ExpenseStatus.APPROVED:
            return False, "Only approved expenses can be marked as paid"
        
        expense.status = ExpenseStatus.PAID
        expense.paid_at = datetime.now()
        expense.payment_reference = payment_reference
        self._add_audit(expense, "PAID", paid_by, f"Ref: {payment_reference}")
        
        return True, f"Expense {expense.expense_number} marked as paid"
    
    # =========================================================================
    # ADVANCE MANAGEMENT
    # =========================================================================
    
    def request_advance(
        self,
        employee_id: str,
        employee_name: str,
        store_id: str,
        advance_type: AdvanceType,
        amount: Decimal,
        purpose: str,
        expected_settlement: date = None
    ) -> Tuple[bool, str, Optional[Advance]]:
        """Request salary/travel advance"""
        
        # Check for outstanding advances
        outstanding = self.get_outstanding_advances(employee_id)
        if outstanding:
            return False, "Cannot request advance while previous advances are outstanding", None
        
        advance = Advance(
            id=str(uuid.uuid4()),
            advance_number=self._gen_advance_number(),
            employee_id=employee_id,
            employee_name=employee_name,
            store_id=store_id,
            advance_type=advance_type,
            amount=amount,
            purpose=purpose,
            requested_date=date.today(),
            expected_settlement_date=expected_settlement
        )
        
        self._add_audit(advance, "REQUESTED", employee_id, f"Amount: ₹{amount}")
        self.advances[advance.id] = advance
        
        return True, f"Advance {advance.advance_number} requested", advance
    
    def approve_advance(self, advance_id: str, approved_by: str) -> Tuple[bool, str]:
        """Approve advance request"""
        advance = self.advances.get(advance_id)
        if not advance:
            return False, "Advance not found"
        
        if advance.status != AdvanceStatus.REQUESTED:
            return False, "Only requested advances can be approved"
        
        advance.status = AdvanceStatus.APPROVED
        advance.approved_by = approved_by
        advance.approved_at = datetime.now()
        self._add_audit(advance, "APPROVED", approved_by)
        
        return True, f"Advance {advance.advance_number} approved"
    
    def disburse_advance(self, advance_id: str, reference: str, disbursed_by: str) -> Tuple[bool, str]:
        """Disburse approved advance"""
        advance = self.advances.get(advance_id)
        if not advance:
            return False, "Advance not found"
        
        if advance.status != AdvanceStatus.APPROVED:
            return False, "Only approved advances can be disbursed"
        
        advance.status = AdvanceStatus.DISBURSED
        advance.disbursed_at = datetime.now()
        advance.disbursement_reference = reference
        self._add_audit(advance, "DISBURSED", disbursed_by, f"Ref: {reference}")
        
        return True, f"Advance {advance.advance_number} disbursed"
    
    def settle_advance_with_expense(self, advance_id: str, expense_id: str) -> Tuple[bool, str]:
        """Link expense to advance for settlement"""
        advance = self.advances.get(advance_id)
        expense = self.expenses.get(expense_id)
        
        if not advance or not expense:
            return False, "Advance or Expense not found"
        
        if advance.status not in [AdvanceStatus.DISBURSED, AdvanceStatus.PARTIALLY_SETTLED]:
            return False, "Advance not in settleable status"
        
        if expense.status != ExpenseStatus.APPROVED:
            return False, "Expense must be approved"
        
        # Link expense to advance
        expense.advance_id = advance_id
        advance.settlement_expenses.append(expense_id)
        advance.settled_amount += expense.amount
        
        # Check if fully settled
        if advance.settled_amount >= advance.amount:
            advance.status = AdvanceStatus.FULLY_SETTLED
        else:
            advance.status = AdvanceStatus.PARTIALLY_SETTLED
        
        self._add_audit(advance, "SETTLEMENT", "SYSTEM", f"Expense {expense.expense_number} linked")
        
        return True, f"Expense linked to advance. Settled: ₹{advance.settled_amount}/{advance.amount}"
    
    def get_outstanding_advances(self, employee_id: str) -> List[Advance]:
        """Get outstanding advances for employee"""
        return [
            adv for adv in self.advances.values()
            if adv.employee_id == employee_id and adv.status in [
                AdvanceStatus.DISBURSED, AdvanceStatus.PARTIALLY_SETTLED
            ]
        ]
    
    # =========================================================================
    # VISIBILITY & QUERIES
    # =========================================================================
    
    def get_expenses_for_approval(self, approver_role: str, store_id: str = None) -> List[Expense]:
        """Get expenses pending approval"""
        expenses = [
            exp for exp in self.expenses.values()
            if exp.status == ExpenseStatus.SUBMITTED
        ]
        
        if store_id:
            expenses = [e for e in expenses if e.store_id == store_id]
        
        # Filter by role authority
        result = []
        for exp in expenses:
            policy = self.policies.get(exp.category)
            if policy and (approver_role in policy.approval_roles or approver_role in ["ADMIN", "SUPERADMIN"]):
                result.append(exp)
        
        return result
    
    def get_employee_expenses(self, employee_id: str, from_date: date = None, to_date: date = None) -> List[Expense]:
        """Get expenses for an employee"""
        expenses = [e for e in self.expenses.values() if e.employee_id == employee_id]
        
        if from_date:
            expenses = [e for e in expenses if e.expense_date >= from_date]
        if to_date:
            expenses = [e for e in expenses if e.expense_date <= to_date]
        
        return sorted(expenses, key=lambda x: x.expense_date, reverse=True)
    
    def get_subordinate_expenses(self, manager_id: str, subordinate_ids: List[str]) -> List[Expense]:
        """Get expenses of subordinates"""
        return [
            e for e in self.expenses.values()
            if e.employee_id in subordinate_ids
        ]
    
    def get_expense_summary(self, employee_id: str) -> Dict:
        """Get expense summary for employee dashboard"""
        expenses = self.get_employee_expenses(employee_id)
        advances = [a for a in self.advances.values() if a.employee_id == employee_id]
        
        return {
            "total_expenses": len(expenses),
            "pending_approval": len([e for e in expenses if e.status == ExpenseStatus.SUBMITTED]),
            "approved": len([e for e in expenses if e.status == ExpenseStatus.APPROVED]),
            "paid": len([e for e in expenses if e.status == ExpenseStatus.PAID]),
            "total_amount_pending": sum(e.amount for e in expenses if e.status in [ExpenseStatus.SUBMITTED, ExpenseStatus.APPROVED]),
            "outstanding_advances": sum(a.amount - a.settled_amount for a in advances if a.status in [AdvanceStatus.DISBURSED, AdvanceStatus.PARTIALLY_SETTLED])
        }


def demo_expense():
    print("=" * 60)
    print("IMS 2.0 EXPENSE & ADVANCE ENGINE DEMO")
    print("=" * 60)
    
    engine = ExpenseEngine()
    
    # Create expense
    print("\n📝 Create Expense")
    success, msg, expense = engine.create_expense(
        "emp-001", "Rahul Singh", "store-001",
        ExpenseCategory.TRAVEL, Decimal("1500"),
        "Travel to vendor for pickup", date.today()
    )
    print(f"  {msg}")
    
    # Upload bill
    print("\n📎 Upload Bill")
    success, msg = engine.upload_bill(
        expense.id, "bill.jpg", "/bills/bill.jpg",
        b"fake_image_content", "image/jpeg", "emp-001"
    )
    print(f"  {msg}")
    
    # Submit
    print("\n📤 Submit Expense")
    success, msg = engine.submit_expense(expense.id)
    print(f"  {msg}")
    
    # Approve
    print("\n✅ Approve Expense")
    success, msg = engine.approve_expense(expense.id, "mgr-001", "STORE_MANAGER")
    print(f"  {msg}")
    
    # Mark paid
    print("\n💰 Mark Paid")
    success, msg = engine.mark_expense_paid(expense.id, "TXN-12345", "finance-001")
    print(f"  {msg}")
    
    # Request advance
    print("\n💵 Request Advance")
    success, msg, advance = engine.request_advance(
        "emp-002", "Priya Gupta", "store-001",
        AdvanceType.SALARY_ADVANCE, Decimal("10000"),
        "Emergency medical expense"
    )
    print(f"  {msg}")
    
    # Approve & disburse
    engine.approve_advance(advance.id, "admin-001")
    success, msg = engine.disburse_advance(advance.id, "NEFT-98765", "finance-001")
    print(f"  Disbursed: {msg}")
    
    # Summary
    print("\n📊 Employee Summary")
    summary = engine.get_expense_summary("emp-001")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    
    # Audit trail
    print("\n📜 Audit Trail")
    for entry in expense.audit_trail:
        print(f"  {entry['action']} by {entry['by']}")
    
    print("\n" + "=" * 60)


if __name__ == "__main__":
    demo_expense()
