"""
IMS 2.0 - Tasks & SOP Engine
=============================
Features:
1. System-generated tasks (auto from deviations)
2. User-created tasks with assignment
3. SOP-bound tasks (daily checklists)
4. Escalation matrix with time-based triggers
5. Priority system (non-customizable colors)
6. Task lifecycle management
7. Recurring tasks
8. Performance tracking
"""

from dataclasses import dataclass, field
from datetime import datetime, date, time, timedelta
from decimal import Decimal
from enum import Enum
from typing import List, Optional, Dict, Callable
import uuid


class TaskPriority(Enum):
    """Non-customizable priority levels with system-enforced colors"""
    P0_BUSINESS_RISK = "P0"    # Dark Red - Business at risk
    P1_URGENT = "P1"           # Red - Urgent
    P2_IMPORTANT = "P2"        # Orange - Important
    P3_NORMAL = "P3"           # Yellow - Normal
    P4_INFORMATIONAL = "P4"    # Blue - Informational


class TaskStatus(Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    COMPLETED = "COMPLETED"
    ESCALATED = "ESCALATED"
    CANCELLED = "CANCELLED"
    FORCE_CLOSED = "FORCE_CLOSED"  # Superadmin only


class TaskSource(Enum):
    SYSTEM = "SYSTEM"          # Auto-generated from deviations
    USER = "USER"              # Created by user
    SOP = "SOP"                # From SOP checklist
    ESCALATION = "ESCALATION"  # Created from escalation


class TaskCategory(Enum):
    STOCK = "STOCK"
    SALES = "SALES"
    PAYMENT = "PAYMENT"
    CLINICAL = "CLINICAL"
    HR = "HR"
    COMPLIANCE = "COMPLIANCE"
    CUSTOMER = "CUSTOMER"
    VENDOR = "VENDOR"
    MAINTENANCE = "MAINTENANCE"
    OTHER = "OTHER"


class EscalationLevel(Enum):
    LEVEL_1 = 1  # Store Manager
    LEVEL_2 = 2  # Area Manager
    LEVEL_3 = 3  # Admin
    LEVEL_4 = 4  # Superadmin


class SOPType(Enum):
    DAILY_OPENING = "DAILY_OPENING"
    DAILY_CLOSING = "DAILY_CLOSING"
    STOCK_ACCEPTANCE = "STOCK_ACCEPTANCE"
    CASH_HANDLING = "CASH_HANDLING"
    CUSTOMER_SERVICE = "CUSTOMER_SERVICE"
    EYE_TEST = "EYE_TEST"
    ORDER_FOLLOWUP = "ORDER_FOLLOWUP"
    DELIVERY = "DELIVERY"


@dataclass
class EscalationRule:
    """Defines when and to whom a task escalates"""
    id: str
    task_category: TaskCategory
    priority: TaskPriority
    
    # Time triggers (minutes)
    acknowledge_within: int       # Must acknowledge within X minutes
    resolve_within: int           # Must resolve within X minutes
    
    # Escalation path
    level_1_role: str = "STORE_MANAGER"
    level_1_after_minutes: int = 60
    
    level_2_role: str = "AREA_MANAGER"
    level_2_after_minutes: int = 120
    
    level_3_role: str = "ADMIN"
    level_3_after_minutes: int = 240
    
    level_4_role: str = "SUPERADMIN"
    level_4_after_minutes: int = 480


@dataclass
class TaskComment:
    id: str
    task_id: str
    user_id: str
    user_name: str
    comment: str
    created_at: datetime = field(default_factory=datetime.now)
    is_system: bool = False


@dataclass
class TaskHistory:
    """Immutable history record"""
    id: str
    task_id: str
    action: str  # CREATED, ASSIGNED, STATUS_CHANGED, ESCALATED, etc.
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    user_id: str = ""
    user_name: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
    notes: Optional[str] = None


@dataclass
class Task:
    id: str
    task_number: str
    
    # Basic info
    title: str
    description: str
    category: TaskCategory
    priority: TaskPriority
    source: TaskSource
    
    # Assignment
    store_id: str
    assigned_to_user_id: Optional[str] = None
    assigned_to_name: Optional[str] = None
    assigned_by_user_id: Optional[str] = None
    
    # Reference (what triggered this task)
    reference_type: Optional[str] = None  # ORDER, STOCK, PAYMENT, etc.
    reference_id: Optional[str] = None
    
    # Status
    status: TaskStatus = TaskStatus.OPEN
    
    # Timing
    created_at: datetime = field(default_factory=datetime.now)
    due_at: Optional[datetime] = None
    acknowledged_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    # Escalation
    current_escalation_level: int = 0
    escalated_to_user_id: Optional[str] = None
    escalated_at: Optional[datetime] = None
    
    # Resolution
    resolution_notes: Optional[str] = None
    resolved_by_user_id: Optional[str] = None
    
    # For SOP tasks
    sop_id: Optional[str] = None
    checklist_items: List[dict] = field(default_factory=list)
    
    # History (immutable)
    history: List[TaskHistory] = field(default_factory=list)
    comments: List[TaskComment] = field(default_factory=list)
    
    # Flags
    is_overdue: bool = False
    is_recurring: bool = False
    recurrence_pattern: Optional[str] = None  # DAILY, WEEKLY, MONTHLY
    
    @property
    def priority_color(self) -> str:
        """System-enforced colors - NOT customizable"""
        colors = {
            TaskPriority.P0_BUSINESS_RISK: "#8B0000",  # Dark Red
            TaskPriority.P1_URGENT: "#FF0000",         # Red
            TaskPriority.P2_IMPORTANT: "#FFA500",      # Orange
            TaskPriority.P3_NORMAL: "#FFD700",         # Yellow/Gold
            TaskPriority.P4_INFORMATIONAL: "#4169E1",  # Royal Blue
        }
        return colors.get(self.priority, "#808080")
    
    @property
    def time_to_due(self) -> Optional[timedelta]:
        if not self.due_at:
            return None
        return self.due_at - datetime.now()
    
    @property
    def is_past_due(self) -> bool:
        if not self.due_at:
            return False
        return datetime.now() > self.due_at


@dataclass
class SOPTemplate:
    """Standard Operating Procedure template"""
    id: str
    sop_type: SOPType
    name: str
    description: str
    
    # Which roles must complete this
    applicable_roles: List[str]
    
    # Checklist items
    checklist: List[dict] = field(default_factory=list)
    # e.g., [{"id": "1", "item": "Count cash drawer", "mandatory": True}, ...]
    
    # Timing
    trigger_time: Optional[time] = None  # When to create task
    completion_window_minutes: int = 60
    
    # Enforcement
    enforcement_level: str = "SOFT"  # SOFT, HARD, MANDATORY
    # SOFT = reminder only
    # HARD = blocks certain actions until complete
    # MANDATORY = escalates if not done
    
    is_active: bool = True


@dataclass
class DailySOPInstance:
    """Instance of SOP for a specific day"""
    id: str
    sop_template_id: str
    sop_type: SOPType
    store_id: str
    instance_date: date
    
    assigned_to_user_id: str
    assigned_to_name: str
    
    # Checklist completion
    checklist_status: List[dict] = field(default_factory=list)
    # [{"item_id": "1", "completed": True, "completed_at": "...", "notes": "..."}]
    
    # Status
    status: str = "PENDING"  # PENDING, IN_PROGRESS, COMPLETED, INCOMPLETE
    
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    # If incomplete
    incomplete_reason: Optional[str] = None
    escalated: bool = False


class TaskEngine:
    """
    Central task management engine.
    Handles task creation, assignment, escalation, and completion.
    """
    
    def __init__(self):
        self.tasks: Dict[str, Task] = {}
        self.sop_templates: Dict[str, SOPTemplate] = {}
        self.sop_instances: Dict[str, DailySOPInstance] = {}
        self.escalation_rules: Dict[str, EscalationRule] = {}
        
        self._task_counter = 0
        self._initialize_default_escalation_rules()
        self._initialize_default_sops()
    
    def _initialize_default_escalation_rules(self):
        """Set up default escalation rules"""
        
        # Stock mismatch - high urgency
        self.escalation_rules["STOCK_P1"] = EscalationRule(
            id="esc-stock-p1",
            task_category=TaskCategory.STOCK,
            priority=TaskPriority.P1_URGENT,
            acknowledge_within=30,
            resolve_within=120,
            level_1_after_minutes=30,
            level_2_after_minutes=60,
            level_3_after_minutes=120,
            level_4_after_minutes=240
        )
        
        # Payment variance
        self.escalation_rules["PAYMENT_P1"] = EscalationRule(
            id="esc-payment-p1",
            task_category=TaskCategory.PAYMENT,
            priority=TaskPriority.P1_URGENT,
            acknowledge_within=15,
            resolve_within=60,
            level_1_after_minutes=15,
            level_2_after_minutes=30,
            level_3_after_minutes=60,
            level_4_after_minutes=120
        )
        
        # Customer complaint
        self.escalation_rules["CUSTOMER_P2"] = EscalationRule(
            id="esc-customer-p2",
            task_category=TaskCategory.CUSTOMER,
            priority=TaskPriority.P2_IMPORTANT,
            acknowledge_within=60,
            resolve_within=480,
            level_1_after_minutes=60,
            level_2_after_minutes=240,
            level_3_after_minutes=480,
            level_4_after_minutes=1440
        )
    
    def _initialize_default_sops(self):
        """Set up default SOP templates"""
        
        # Daily Opening SOP
        opening_sop = SOPTemplate(
            id="sop-daily-opening",
            sop_type=SOPType.DAILY_OPENING,
            name="Daily Store Opening",
            description="Tasks to complete when opening the store",
            applicable_roles=["STORE_MANAGER", "SALES_CASHIER"],
            checklist=[
                {"id": "1", "item": "Unlock store and disable alarm", "mandatory": True},
                {"id": "2", "item": "Turn on all lights and displays", "mandatory": True},
                {"id": "3", "item": "Count opening cash drawer", "mandatory": True},
                {"id": "4", "item": "Check and record opening balance", "mandatory": True},
                {"id": "5", "item": "Verify stock on display", "mandatory": False},
                {"id": "6", "item": "Check pending orders for today", "mandatory": True},
                {"id": "7", "item": "Review staff schedule", "mandatory": False},
                {"id": "8", "item": "Check cleaning status", "mandatory": False},
            ],
            trigger_time=time(9, 30),
            completion_window_minutes=60,
            enforcement_level="MANDATORY"
        )
        self.sop_templates[opening_sop.id] = opening_sop
        
        # Daily Closing SOP
        closing_sop = SOPTemplate(
            id="sop-daily-closing",
            sop_type=SOPType.DAILY_CLOSING,
            name="Daily Store Closing",
            description="Tasks to complete when closing the store",
            applicable_roles=["STORE_MANAGER", "SALES_CASHIER"],
            checklist=[
                {"id": "1", "item": "Complete all pending transactions", "mandatory": True},
                {"id": "2", "item": "Count cash drawer", "mandatory": True},
                {"id": "3", "item": "Record closing balance", "mandatory": True},
                {"id": "4", "item": "Reconcile with system", "mandatory": True},
                {"id": "5", "item": "Deposit cash in safe", "mandatory": True},
                {"id": "6", "item": "Update pending order statuses", "mandatory": True},
                {"id": "7", "item": "Turn off displays and non-essential lights", "mandatory": True},
                {"id": "8", "item": "Set alarm and lock store", "mandatory": True},
            ],
            trigger_time=time(19, 0),
            completion_window_minutes=60,
            enforcement_level="MANDATORY"
        )
        self.sop_templates[closing_sop.id] = closing_sop
        
        # Stock Acceptance SOP
        stock_sop = SOPTemplate(
            id="sop-stock-acceptance",
            sop_type=SOPType.STOCK_ACCEPTANCE,
            name="Stock Acceptance Procedure",
            description="Steps for accepting incoming stock",
            applicable_roles=["STORE_MANAGER"],
            checklist=[
                {"id": "1", "item": "Verify document/challan against physical", "mandatory": True},
                {"id": "2", "item": "Count all items received", "mandatory": True},
                {"id": "3", "item": "Check for damages", "mandatory": True},
                {"id": "4", "item": "Verify barcodes match documents", "mandatory": True},
                {"id": "5", "item": "Assign location codes", "mandatory": True},
                {"id": "6", "item": "Print store barcodes", "mandatory": True},
                {"id": "7", "item": "Update system with acceptance", "mandatory": True},
                {"id": "8", "item": "Report any discrepancies", "mandatory": True},
            ],
            completion_window_minutes=120,
            enforcement_level="HARD"
        )
        self.sop_templates[stock_sop.id] = stock_sop
    
    def generate_task_number(self) -> str:
        self._task_counter += 1
        return f"TASK-{datetime.now().strftime('%Y%m%d')}-{self._task_counter:04d}"
    
    # =========================================================================
    # TASK CREATION
    # =========================================================================
    
    def create_system_task(
        self,
        title: str,
        description: str,
        category: TaskCategory,
        priority: TaskPriority,
        store_id: str,
        reference_type: Optional[str] = None,
        reference_id: Optional[str] = None,
        due_in_minutes: int = 60
    ) -> Task:
        """Create a system-generated task (from deviation detection)"""
        
        task = Task(
            id=str(uuid.uuid4()),
            task_number=self.generate_task_number(),
            title=title,
            description=description,
            category=category,
            priority=priority,
            source=TaskSource.SYSTEM,
            store_id=store_id,
            reference_type=reference_type,
            reference_id=reference_id,
            due_at=datetime.now() + timedelta(minutes=due_in_minutes)
        )
        
        # Add creation history
        task.history.append(TaskHistory(
            id=str(uuid.uuid4()),
            task_id=task.id,
            action="CREATED",
            new_value=f"System task created: {title}",
            user_id="SYSTEM",
            user_name="System"
        ))
        
        self.tasks[task.id] = task
        return task
    
    def create_user_task(
        self,
        title: str,
        description: str,
        category: TaskCategory,
        priority: TaskPriority,
        store_id: str,
        created_by_user_id: str,
        created_by_name: str,
        assigned_to_user_id: Optional[str] = None,
        assigned_to_name: Optional[str] = None,
        due_at: Optional[datetime] = None
    ) -> Task:
        """Create a user-generated task"""
        
        task = Task(
            id=str(uuid.uuid4()),
            task_number=self.generate_task_number(),
            title=title,
            description=description,
            category=category,
            priority=priority,
            source=TaskSource.USER,
            store_id=store_id,
            assigned_to_user_id=assigned_to_user_id,
            assigned_to_name=assigned_to_name,
            assigned_by_user_id=created_by_user_id,
            due_at=due_at
        )
        
        task.history.append(TaskHistory(
            id=str(uuid.uuid4()),
            task_id=task.id,
            action="CREATED",
            new_value=f"Task created by {created_by_name}",
            user_id=created_by_user_id,
            user_name=created_by_name
        ))
        
        if assigned_to_user_id:
            task.history.append(TaskHistory(
                id=str(uuid.uuid4()),
                task_id=task.id,
                action="ASSIGNED",
                new_value=assigned_to_name,
                user_id=created_by_user_id,
                user_name=created_by_name
            ))
        
        self.tasks[task.id] = task
        return task
    
    # =========================================================================
    # TASK ASSIGNMENT
    # =========================================================================
    
    def assign_task(
        self,
        task_id: str,
        assigned_to_user_id: str,
        assigned_to_name: str,
        assigned_by_user_id: str,
        assigned_by_name: str
    ) -> tuple:
        """Assign or reassign a task"""
        
        task = self.tasks.get(task_id)
        if not task:
            return False, "Task not found"
        
        if task.status in [TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FORCE_CLOSED]:
            return False, f"Cannot assign task in {task.status.value} status"
        
        old_assignee = task.assigned_to_name
        
        task.assigned_to_user_id = assigned_to_user_id
        task.assigned_to_name = assigned_to_name
        task.assigned_by_user_id = assigned_by_user_id
        
        task.history.append(TaskHistory(
            id=str(uuid.uuid4()),
            task_id=task.id,
            action="REASSIGNED" if old_assignee else "ASSIGNED",
            old_value=old_assignee,
            new_value=assigned_to_name,
            user_id=assigned_by_user_id,
            user_name=assigned_by_name
        ))
        
        return True, f"Task assigned to {assigned_to_name}"
    
    # =========================================================================
    # TASK STATUS UPDATES
    # =========================================================================
    
    def acknowledge_task(
        self,
        task_id: str,
        user_id: str,
        user_name: str
    ) -> tuple:
        """Acknowledge receipt of task"""
        
        task = self.tasks.get(task_id)
        if not task:
            return False, "Task not found"
        
        if task.acknowledged_at:
            return False, "Task already acknowledged"
        
        task.acknowledged_at = datetime.now()
        
        task.history.append(TaskHistory(
            id=str(uuid.uuid4()),
            task_id=task.id,
            action="ACKNOWLEDGED",
            user_id=user_id,
            user_name=user_name
        ))
        
        return True, "Task acknowledged"
    
    def start_task(
        self,
        task_id: str,
        user_id: str,
        user_name: str
    ) -> tuple:
        """Mark task as in progress"""
        
        task = self.tasks.get(task_id)
        if not task:
            return False, "Task not found"
        
        if task.status != TaskStatus.OPEN:
            return False, f"Cannot start task in {task.status.value} status"
        
        old_status = task.status.value
        task.status = TaskStatus.IN_PROGRESS
        task.started_at = datetime.now()
        
        if not task.acknowledged_at:
            task.acknowledged_at = datetime.now()
        
        task.history.append(TaskHistory(
            id=str(uuid.uuid4()),
            task_id=task.id,
            action="STATUS_CHANGED",
            old_value=old_status,
            new_value=TaskStatus.IN_PROGRESS.value,
            user_id=user_id,
            user_name=user_name
        ))
        
        return True, "Task started"
    
    def complete_task(
        self,
        task_id: str,
        user_id: str,
        user_name: str,
        resolution_notes: str
    ) -> tuple:
        """Mark task as completed"""
        
        task = self.tasks.get(task_id)
        if not task:
            return False, "Task not found"
        
        if task.status in [TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FORCE_CLOSED]:
            return False, f"Task already {task.status.value}"
        
        # Check if SOP task has all mandatory items completed
        if task.source == TaskSource.SOP and task.checklist_items:
            for item in task.checklist_items:
                if item.get("mandatory") and not item.get("completed"):
                    return False, f"Mandatory checklist item not completed: {item.get('item')}"
        
        old_status = task.status.value
        task.status = TaskStatus.COMPLETED
        task.completed_at = datetime.now()
        task.resolution_notes = resolution_notes
        task.resolved_by_user_id = user_id
        
        task.history.append(TaskHistory(
            id=str(uuid.uuid4()),
            task_id=task.id,
            action="COMPLETED",
            old_value=old_status,
            new_value=TaskStatus.COMPLETED.value,
            user_id=user_id,
            user_name=user_name,
            notes=resolution_notes
        ))
        
        return True, "Task completed"
    
    def force_close_task(
        self,
        task_id: str,
        user_id: str,
        user_name: str,
        reason: str,
        user_role: str
    ) -> tuple:
        """Force close a task (Superadmin only)"""
        
        if user_role != "SUPERADMIN":
            return False, "Only Superadmin can force close tasks"
        
        task = self.tasks.get(task_id)
        if not task:
            return False, "Task not found"
        
        old_status = task.status.value
        task.status = TaskStatus.FORCE_CLOSED
        task.completed_at = datetime.now()
        task.resolution_notes = f"FORCE CLOSED: {reason}"
        task.resolved_by_user_id = user_id
        
        task.history.append(TaskHistory(
            id=str(uuid.uuid4()),
            task_id=task.id,
            action="FORCE_CLOSED",
            old_value=old_status,
            new_value=TaskStatus.FORCE_CLOSED.value,
            user_id=user_id,
            user_name=user_name,
            notes=f"Force closed by Superadmin: {reason}"
        ))
        
        return True, "Task force closed"
    
    # =========================================================================
    # ESCALATION
    # =========================================================================
    
    def check_and_escalate(self, task_id: str) -> tuple:
        """Check if task needs escalation and escalate if necessary"""
        
        task = self.tasks.get(task_id)
        if not task:
            return False, "Task not found"
        
        if task.status in [TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FORCE_CLOSED]:
            return False, "Task already closed"
        
        # Get escalation rule
        rule_key = f"{task.category.value}_{task.priority.value}"
        rule = self.escalation_rules.get(rule_key)
        
        if not rule:
            return False, "No escalation rule found"
        
        minutes_since_creation = (datetime.now() - task.created_at).total_seconds() / 60
        
        # Determine escalation level
        new_level = 0
        escalate_to_role = None
        
        if minutes_since_creation >= rule.level_4_after_minutes and task.current_escalation_level < 4:
            new_level = 4
            escalate_to_role = rule.level_4_role
        elif minutes_since_creation >= rule.level_3_after_minutes and task.current_escalation_level < 3:
            new_level = 3
            escalate_to_role = rule.level_3_role
        elif minutes_since_creation >= rule.level_2_after_minutes and task.current_escalation_level < 2:
            new_level = 2
            escalate_to_role = rule.level_2_role
        elif minutes_since_creation >= rule.level_1_after_minutes and task.current_escalation_level < 1:
            new_level = 1
            escalate_to_role = rule.level_1_role
        
        if new_level > task.current_escalation_level:
            old_level = task.current_escalation_level
            task.current_escalation_level = new_level
            task.status = TaskStatus.ESCALATED
            task.escalated_at = datetime.now()
            
            task.history.append(TaskHistory(
                id=str(uuid.uuid4()),
                task_id=task.id,
                action="ESCALATED",
                old_value=f"Level {old_level}",
                new_value=f"Level {new_level} ({escalate_to_role})",
                user_id="SYSTEM",
                user_name="System"
            ))
            
            return True, f"Task escalated to Level {new_level} ({escalate_to_role})"
        
        return False, "No escalation needed"
    
    def process_all_escalations(self) -> List[dict]:
        """Process escalations for all open tasks"""
        
        escalated = []
        
        for task_id, task in self.tasks.items():
            if task.status in [TaskStatus.OPEN, TaskStatus.IN_PROGRESS, TaskStatus.ESCALATED]:
                success, msg = self.check_and_escalate(task_id)
                if success:
                    escalated.append({
                        "task_id": task_id,
                        "task_number": task.task_number,
                        "message": msg
                    })
        
        return escalated
    
    # =========================================================================
    # SOP MANAGEMENT
    # =========================================================================
    
    def create_daily_sop_tasks(
        self,
        store_id: str,
        sop_type: SOPType,
        assigned_to_user_id: str,
        assigned_to_name: str
    ) -> Task:
        """Create a task from SOP template for today"""
        
        # Find template
        template = None
        for t in self.sop_templates.values():
            if t.sop_type == sop_type and t.is_active:
                template = t
                break
        
        if not template:
            return None
        
        # Calculate due time
        due_at = None
        if template.trigger_time:
            due_at = datetime.combine(
                date.today(),
                template.trigger_time
            ) + timedelta(minutes=template.completion_window_minutes)
        else:
            due_at = datetime.now() + timedelta(minutes=template.completion_window_minutes)
        
        # Create task with checklist
        task = Task(
            id=str(uuid.uuid4()),
            task_number=self.generate_task_number(),
            title=template.name,
            description=template.description,
            category=TaskCategory.COMPLIANCE,
            priority=TaskPriority.P2_IMPORTANT if template.enforcement_level == "MANDATORY" else TaskPriority.P3_NORMAL,
            source=TaskSource.SOP,
            store_id=store_id,
            assigned_to_user_id=assigned_to_user_id,
            assigned_to_name=assigned_to_name,
            sop_id=template.id,
            checklist_items=[
                {**item, "completed": False, "completed_at": None, "notes": None}
                for item in template.checklist
            ],
            due_at=due_at
        )
        
        task.history.append(TaskHistory(
            id=str(uuid.uuid4()),
            task_id=task.id,
            action="CREATED",
            new_value=f"SOP Task: {template.name}",
            user_id="SYSTEM",
            user_name="System"
        ))
        
        self.tasks[task.id] = task
        return task
    
    def complete_checklist_item(
        self,
        task_id: str,
        item_id: str,
        user_id: str,
        user_name: str,
        notes: Optional[str] = None
    ) -> tuple:
        """Mark a checklist item as complete"""
        
        task = self.tasks.get(task_id)
        if not task:
            return False, "Task not found"
        
        for item in task.checklist_items:
            if item.get("id") == item_id:
                if item.get("completed"):
                    return False, "Item already completed"
                
                item["completed"] = True
                item["completed_at"] = datetime.now().isoformat()
                item["completed_by"] = user_id
                item["notes"] = notes
                
                task.history.append(TaskHistory(
                    id=str(uuid.uuid4()),
                    task_id=task.id,
                    action="CHECKLIST_ITEM_COMPLETED",
                    new_value=item.get("item"),
                    user_id=user_id,
                    user_name=user_name
                ))
                
                return True, f"Checklist item completed: {item.get('item')}"
        
        return False, "Checklist item not found"
    
    # =========================================================================
    # COMMENTS
    # =========================================================================
    
    def add_comment(
        self,
        task_id: str,
        user_id: str,
        user_name: str,
        comment: str
    ) -> tuple:
        """Add a comment to a task"""
        
        task = self.tasks.get(task_id)
        if not task:
            return False, "Task not found"
        
        task_comment = TaskComment(
            id=str(uuid.uuid4()),
            task_id=task_id,
            user_id=user_id,
            user_name=user_name,
            comment=comment
        )
        
        task.comments.append(task_comment)
        
        return True, "Comment added"
    
    # =========================================================================
    # QUERIES
    # =========================================================================
    
    def get_user_tasks(
        self,
        user_id: str,
        status_filter: Optional[List[TaskStatus]] = None,
        include_unassigned: bool = False
    ) -> List[Task]:
        """Get tasks assigned to a user"""
        
        result = []
        
        for task in self.tasks.values():
            if task.assigned_to_user_id == user_id:
                if status_filter is None or task.status in status_filter:
                    result.append(task)
            elif include_unassigned and task.assigned_to_user_id is None:
                if status_filter is None or task.status in status_filter:
                    result.append(task)
        
        # Sort by priority and due date
        return sorted(result, key=lambda t: (t.priority.value, t.due_at or datetime.max))
    
    def get_store_tasks(
        self,
        store_id: str,
        status_filter: Optional[List[TaskStatus]] = None
    ) -> List[Task]:
        """Get all tasks for a store"""
        
        result = []
        
        for task in self.tasks.values():
            if task.store_id == store_id:
                if status_filter is None or task.status in status_filter:
                    result.append(task)
        
        return sorted(result, key=lambda t: (t.priority.value, t.due_at or datetime.max))
    
    def get_overdue_tasks(self, store_id: Optional[str] = None) -> List[Task]:
        """Get all overdue tasks"""
        
        result = []
        
        for task in self.tasks.values():
            if task.is_past_due and task.status not in [
                TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FORCE_CLOSED
            ]:
                if store_id is None or task.store_id == store_id:
                    task.is_overdue = True
                    result.append(task)
        
        return sorted(result, key=lambda t: t.due_at or datetime.max)
    
    def get_task_statistics(self, store_id: str) -> dict:
        """Get task statistics for a store"""
        
        stats = {
            "total": 0,
            "open": 0,
            "in_progress": 0,
            "completed": 0,
            "escalated": 0,
            "overdue": 0,
            "by_priority": {p.value: 0 for p in TaskPriority},
            "by_category": {c.value: 0 for c in TaskCategory}
        }
        
        for task in self.tasks.values():
            if task.store_id == store_id:
                stats["total"] += 1
                
                if task.status == TaskStatus.OPEN:
                    stats["open"] += 1
                elif task.status == TaskStatus.IN_PROGRESS:
                    stats["in_progress"] += 1
                elif task.status == TaskStatus.COMPLETED:
                    stats["completed"] += 1
                elif task.status == TaskStatus.ESCALATED:
                    stats["escalated"] += 1
                
                if task.is_past_due and task.status not in [
                    TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FORCE_CLOSED
                ]:
                    stats["overdue"] += 1
                
                stats["by_priority"][task.priority.value] += 1
                stats["by_category"][task.category.value] += 1
        
        return stats


# =============================================================================
# DEMO
# =============================================================================

def demo_tasks():
    """Demonstrate task management"""
    
    print("=" * 70)
    print("IMS 2.0 TASKS & SOP ENGINE - DEMO")
    print("=" * 70)
    
    engine = TaskEngine()
    
    # -------------------------------------------------------------------------
    # SCENARIO 1: System-generated task (stock mismatch)
    # -------------------------------------------------------------------------
    print("\n📋 SCENARIO 1: System Task (Stock Mismatch)")
    print("-" * 50)
    
    task1 = engine.create_system_task(
        title="Stock Mismatch Detected",
        description="Physical count shows 5 Ray-Ban frames but system shows 7. Variance: -2 units.",
        category=TaskCategory.STOCK,
        priority=TaskPriority.P1_URGENT,
        store_id="store-bv-001",
        reference_type="STOCK_COUNT",
        reference_id="SC-001",
        due_in_minutes=60
    )
    print(f"Task Created: {task1.task_number}")
    print(f"Priority: {task1.priority.value} (Color: {task1.priority_color})")
    print(f"Due: {task1.due_at}")
    
    # Assign task
    success, msg = engine.assign_task(
        task_id=task1.id,
        assigned_to_user_id="user-manager",
        assigned_to_name="Neha Sharma",
        assigned_by_user_id="SYSTEM",
        assigned_by_name="System"
    )
    print(f"Assignment: {msg}")
    
    # -------------------------------------------------------------------------
    # SCENARIO 2: User-created task
    # -------------------------------------------------------------------------
    print("\n📋 SCENARIO 2: User Task (Customer Follow-up)")
    print("-" * 50)
    
    task2 = engine.create_user_task(
        title="Customer Follow-up: Order Delay",
        description="Customer Rajesh Kumar called about delayed order. Follow up with workshop.",
        category=TaskCategory.CUSTOMER,
        priority=TaskPriority.P2_IMPORTANT,
        store_id="store-bv-001",
        created_by_user_id="user-manager",
        created_by_name="Neha Sharma",
        assigned_to_user_id="user-sales",
        assigned_to_name="Rahul Kumar",
        due_at=datetime.now() + timedelta(hours=4)
    )
    print(f"Task Created: {task2.task_number}")
    print(f"Assigned To: {task2.assigned_to_name}")
    
    # -------------------------------------------------------------------------
    # SCENARIO 3: SOP Task (Daily Opening)
    # -------------------------------------------------------------------------
    print("\n📋 SCENARIO 3: SOP Task (Daily Opening Checklist)")
    print("-" * 50)
    
    task3 = engine.create_daily_sop_tasks(
        store_id="store-bv-001",
        sop_type=SOPType.DAILY_OPENING,
        assigned_to_user_id="user-cashier",
        assigned_to_name="Priya Singh"
    )
    print(f"SOP Task Created: {task3.task_number}")
    print(f"Checklist Items: {len(task3.checklist_items)}")
    
    # Complete some checklist items
    for i, item in enumerate(task3.checklist_items[:4]):
        success, msg = engine.complete_checklist_item(
            task_id=task3.id,
            item_id=item["id"],
            user_id="user-cashier",
            user_name="Priya Singh"
        )
        print(f"  ✓ {item['item']}")
    
    # -------------------------------------------------------------------------
    # SCENARIO 4: Task Workflow
    # -------------------------------------------------------------------------
    print("\n📋 SCENARIO 4: Task Workflow")
    print("-" * 50)
    
    # Acknowledge
    success, msg = engine.acknowledge_task(task1.id, "user-manager", "Neha Sharma")
    print(f"Acknowledge: {msg}")
    
    # Start
    success, msg = engine.start_task(task1.id, "user-manager", "Neha Sharma")
    print(f"Start: {msg}")
    
    # Add comment
    success, msg = engine.add_comment(
        task_id=task1.id,
        user_id="user-manager",
        user_name="Neha Sharma",
        comment="Checked CCTV footage. Investigating possible theft."
    )
    print(f"Comment: {msg}")
    
    # Complete
    success, msg = engine.complete_task(
        task_id=task1.id,
        user_id="user-manager",
        user_name="Neha Sharma",
        resolution_notes="Found 2 frames in back storage. Stock now matches."
    )
    print(f"Complete: {msg}")
    
    # -------------------------------------------------------------------------
    # SCENARIO 5: Escalation
    # -------------------------------------------------------------------------
    print("\n📋 SCENARIO 5: Escalation Check")
    print("-" * 50)
    
    # Create old task that should escalate
    old_task = engine.create_system_task(
        title="Cash Variance Detected",
        description="Till shows ₹500 variance",
        category=TaskCategory.PAYMENT,
        priority=TaskPriority.P1_URGENT,
        store_id="store-bv-001",
        due_in_minutes=30
    )
    # Simulate old task
    old_task.created_at = datetime.now() - timedelta(minutes=120)
    
    success, msg = engine.check_and_escalate(old_task.id)
    print(f"Escalation: {msg}")
    print(f"Current Level: {old_task.current_escalation_level}")
    
    # -------------------------------------------------------------------------
    # SCENARIO 6: Task Statistics
    # -------------------------------------------------------------------------
    print("\n📋 SCENARIO 6: Task Statistics")
    print("-" * 50)
    
    stats = engine.get_task_statistics("store-bv-001")
    print(f"Total Tasks: {stats['total']}")
    print(f"Open: {stats['open']}")
    print(f"In Progress: {stats['in_progress']}")
    print(f"Completed: {stats['completed']}")
    print(f"Escalated: {stats['escalated']}")
    print(f"Overdue: {stats['overdue']}")
    
    # -------------------------------------------------------------------------
    # SCENARIO 7: Force Close (Superadmin)
    # -------------------------------------------------------------------------
    print("\n📋 SCENARIO 7: Force Close (Superadmin Only)")
    print("-" * 50)
    
    # Try with non-superadmin
    success, msg = engine.force_close_task(
        task_id=task2.id,
        user_id="user-manager",
        user_name="Neha Sharma",
        reason="Customer issue resolved offline",
        user_role="STORE_MANAGER"
    )
    print(f"Manager attempt: {msg}")
    
    # Try with superadmin
    success, msg = engine.force_close_task(
        task_id=task2.id,
        user_id="user-superadmin",
        user_name="Brashak G",
        reason="Customer issue resolved offline",
        user_role="SUPERADMIN"
    )
    print(f"Superadmin attempt: {msg}")
    
    print("\n" + "=" * 70)
    print("END OF DEMO")
    print("=" * 70)


if __name__ == "__main__":
    demo_tasks()
