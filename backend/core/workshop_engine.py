"""
IMS 2.0 - Workshop & Job Engine
================================
Lens fitting, repairs, job tracking, workshop management
"""
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from decimal import Decimal
from enum import Enum
from typing import List, Optional, Dict, Tuple
import uuid

class JobType(Enum):
    LENS_FITTING = "LENS_FITTING"
    FRAME_REPAIR = "FRAME_REPAIR"
    LENS_REPLACEMENT = "LENS_REPLACEMENT"
    NOSE_PAD_REPLACEMENT = "NOSE_PAD_REPLACEMENT"
    SCREW_REPLACEMENT = "SCREW_REPLACEMENT"
    FRAME_ADJUSTMENT = "FRAME_ADJUSTMENT"
    WATCH_BATTERY = "WATCH_BATTERY"
    WATCH_STRAP = "WATCH_STRAP"
    WATCH_REPAIR = "WATCH_REPAIR"
    OTHER = "OTHER"

class JobStatus(Enum):
    CREATED = "CREATED"
    LENS_ORDERED = "LENS_ORDERED"
    LENS_RECEIVED = "LENS_RECEIVED"
    IN_PROGRESS = "IN_PROGRESS"
    QC_PENDING = "QC_PENDING"
    QC_PASSED = "QC_PASSED"
    QC_FAILED = "QC_FAILED"
    READY = "READY"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"

class JobPriority(Enum):
    NORMAL = "NORMAL"
    EXPRESS = "EXPRESS"
    URGENT = "URGENT"

@dataclass
class LensOrder:
    id: str
    job_id: str
    vendor_id: str
    vendor_name: str
    
    # Lens details
    lens_type: str
    lens_brand: str
    coating: str
    
    # Power (Right)
    r_sph: Decimal = Decimal("0")
    r_cyl: Decimal = Decimal("0")
    r_axis: int = 0
    r_add: Decimal = Decimal("0")
    
    # Power (Left)
    l_sph: Decimal = Decimal("0")
    l_cyl: Decimal = Decimal("0")
    l_axis: int = 0
    l_add: Decimal = Decimal("0")
    
    # Status
    ordered_at: datetime = field(default_factory=datetime.now)
    expected_date: Optional[date] = None
    received_at: Optional[datetime] = None
    status: str = "ORDERED"

@dataclass 
class Job:
    id: str
    job_number: str
    job_type: JobType
    
    # Order reference
    order_id: str
    order_number: str
    
    # Customer
    customer_id: str
    customer_name: str
    customer_phone: str
    
    # Store
    store_id: str
    
    # Optional fields with defaults
    invoice_id: Optional[str] = None
    frame_barcode: Optional[str] = None
    frame_name: Optional[str] = None
    prescription_id: Optional[str] = None
    lens_order: Optional[LensOrder] = None
    assigned_to: Optional[str] = None
    assigned_name: Optional[str] = None
    
    # Status
    status: JobStatus = JobStatus.CREATED
    priority: JobPriority = JobPriority.NORMAL
    
    # Dates
    created_at: datetime = field(default_factory=datetime.now)
    expected_date: Optional[date] = None
    promised_date: Optional[date] = None
    completed_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    
    # Notes
    notes: str = ""
    qc_notes: str = ""
    
    # Status history
    status_history: List[Dict] = field(default_factory=list)

@dataclass
class QCChecklist:
    job_id: str
    checked_by: str
    checked_at: datetime
    
    # Checks
    power_verified: bool = False
    axis_verified: bool = False
    pd_verified: bool = False
    fitting_checked: bool = False
    cosmetic_checked: bool = False
    cleaning_done: bool = False
    
    passed: bool = False
    remarks: str = ""


class WorkshopEngine:
    def __init__(self):
        self.jobs: Dict[str, Job] = {}
        self.lens_orders: Dict[str, LensOrder] = {}
        self.qc_checklists: Dict[str, QCChecklist] = {}
        self._job_counter = 0
    
    def _gen_job_number(self, store_code: str) -> str:
        self._job_counter += 1
        return f"JOB-{store_code}-{date.today().strftime('%Y%m%d')}-{self._job_counter:04d}"
    
    def create_job(self, job_type: JobType, order_id: str, order_number: str,
                   customer_id: str, customer_name: str, customer_phone: str,
                   store_id: str, store_code: str,
                   frame_barcode: str = None, prescription_id: str = None,
                   priority: JobPriority = JobPriority.NORMAL) -> Job:
        
        job = Job(
            id=str(uuid.uuid4()),
            job_number=self._gen_job_number(store_code),
            job_type=job_type,
            order_id=order_id,
            order_number=order_number,
            customer_id=customer_id,
            customer_name=customer_name,
            customer_phone=customer_phone,
            store_id=store_id,
            frame_barcode=frame_barcode,
            prescription_id=prescription_id,
            priority=priority
        )
        
        # Set expected date based on priority
        if priority == JobPriority.URGENT:
            job.expected_date = date.today() + timedelta(days=1)
        elif priority == JobPriority.EXPRESS:
            job.expected_date = date.today() + timedelta(days=3)
        else:
            job.expected_date = date.today() + timedelta(days=7)
        
        job.promised_date = job.expected_date
        
        self._add_status_history(job, JobStatus.CREATED, "Job created")
        self.jobs[job.id] = job
        return job
    
    def _add_status_history(self, job: Job, status: JobStatus, note: str = ""):
        job.status_history.append({
            "status": status.value,
            "timestamp": datetime.now().isoformat(),
            "note": note
        })
    
    def order_lens(self, job_id: str, vendor_id: str, vendor_name: str,
                   lens_type: str, lens_brand: str, coating: str,
                   r_sph: Decimal, r_cyl: Decimal, r_axis: int, r_add: Decimal,
                   l_sph: Decimal, l_cyl: Decimal, l_axis: int, l_add: Decimal,
                   expected_days: int = 5) -> Tuple[bool, str, Optional[LensOrder]]:
        
        job = self.jobs.get(job_id)
        if not job:
            return False, "Job not found", None
        
        lens_order = LensOrder(
            id=str(uuid.uuid4()),
            job_id=job_id,
            vendor_id=vendor_id,
            vendor_name=vendor_name,
            lens_type=lens_type,
            lens_brand=lens_brand,
            coating=coating,
            r_sph=r_sph, r_cyl=r_cyl, r_axis=r_axis, r_add=r_add,
            l_sph=l_sph, l_cyl=l_cyl, l_axis=l_axis, l_add=l_add,
            expected_date=date.today() + timedelta(days=expected_days)
        )
        
        job.lens_order = lens_order
        job.status = JobStatus.LENS_ORDERED
        self._add_status_history(job, JobStatus.LENS_ORDERED, f"Lens ordered from {vendor_name}")
        
        self.lens_orders[lens_order.id] = lens_order
        return True, f"Lens ordered. Expected: {lens_order.expected_date}", lens_order
    
    def receive_lens(self, lens_order_id: str) -> Tuple[bool, str]:
        lens_order = self.lens_orders.get(lens_order_id)
        if not lens_order:
            return False, "Lens order not found"
        
        lens_order.received_at = datetime.now()
        lens_order.status = "RECEIVED"
        
        job = self.jobs.get(lens_order.job_id)
        if job:
            job.status = JobStatus.LENS_RECEIVED
            self._add_status_history(job, JobStatus.LENS_RECEIVED, "Lens received from vendor")
        
        return True, "Lens received"
    
    def assign_job(self, job_id: str, staff_id: str, staff_name: str) -> Tuple[bool, str]:
        job = self.jobs.get(job_id)
        if not job:
            return False, "Job not found"
        
        job.assigned_to = staff_id
        job.assigned_name = staff_name
        return True, f"Job assigned to {staff_name}"
    
    def start_job(self, job_id: str) -> Tuple[bool, str]:
        job = self.jobs.get(job_id)
        if not job:
            return False, "Job not found"
        
        if job.job_type == JobType.LENS_FITTING and job.status != JobStatus.LENS_RECEIVED:
            return False, "Lens not yet received"
        
        job.status = JobStatus.IN_PROGRESS
        self._add_status_history(job, JobStatus.IN_PROGRESS, "Work started")
        return True, "Job started"
    
    def complete_job(self, job_id: str) -> Tuple[bool, str]:
        job = self.jobs.get(job_id)
        if not job:
            return False, "Job not found"
        
        job.status = JobStatus.QC_PENDING
        job.completed_at = datetime.now()
        self._add_status_history(job, JobStatus.QC_PENDING, "Work completed, pending QC")
        return True, "Job completed, pending QC"
    
    def perform_qc(self, job_id: str, checked_by: str,
                   power_ok: bool, axis_ok: bool, pd_ok: bool,
                   fitting_ok: bool, cosmetic_ok: bool,
                   remarks: str = "") -> Tuple[bool, str]:
        
        job = self.jobs.get(job_id)
        if not job:
            return False, "Job not found"
        
        passed = all([power_ok, axis_ok, pd_ok, fitting_ok, cosmetic_ok])
        
        qc = QCChecklist(
            job_id=job_id,
            checked_by=checked_by,
            checked_at=datetime.now(),
            power_verified=power_ok,
            axis_verified=axis_ok,
            pd_verified=pd_ok,
            fitting_checked=fitting_ok,
            cosmetic_checked=cosmetic_ok,
            cleaning_done=True,
            passed=passed,
            remarks=remarks
        )
        
        self.qc_checklists[job_id] = qc
        
        if passed:
            job.status = JobStatus.READY
            job.qc_notes = "QC Passed"
            self._add_status_history(job, JobStatus.READY, "QC passed, ready for delivery")
            return True, "QC passed. Ready for delivery"
        else:
            job.status = JobStatus.QC_FAILED
            job.qc_notes = remarks
            self._add_status_history(job, JobStatus.QC_FAILED, f"QC failed: {remarks}")
            return False, f"QC failed: {remarks}"
    
    def deliver_job(self, job_id: str) -> Tuple[bool, str]:
        job = self.jobs.get(job_id)
        if not job:
            return False, "Job not found"
        
        if job.status != JobStatus.READY:
            return False, "Job not ready for delivery"
        
        job.status = JobStatus.DELIVERED
        job.delivered_at = datetime.now()
        self._add_status_history(job, JobStatus.DELIVERED, "Delivered to customer")
        return True, "Job delivered"
    
    def get_pending_jobs(self, store_id: str) -> List[Job]:
        return [j for j in self.jobs.values() 
                if j.store_id == store_id and j.status not in [JobStatus.DELIVERED, JobStatus.CANCELLED]]
    
    def get_job_stats(self, store_id: str) -> Dict:
        jobs = [j for j in self.jobs.values() if j.store_id == store_id]
        
        return {
            "total": len(jobs),
            "pending_lens": len([j for j in jobs if j.status == JobStatus.LENS_ORDERED]),
            "in_progress": len([j for j in jobs if j.status == JobStatus.IN_PROGRESS]),
            "qc_pending": len([j for j in jobs if j.status == JobStatus.QC_PENDING]),
            "ready": len([j for j in jobs if j.status == JobStatus.READY]),
            "delivered": len([j for j in jobs if j.status == JobStatus.DELIVERED]),
            "overdue": len([j for j in jobs if j.promised_date and j.promised_date < date.today() 
                           and j.status not in [JobStatus.DELIVERED, JobStatus.CANCELLED]])
        }


def demo_workshop():
    print("=" * 60)
    print("IMS 2.0 WORKSHOP ENGINE DEMO")
    print("=" * 60)
    
    engine = WorkshopEngine()
    
    # Create job
    print("\n🔧 Create Lens Fitting Job")
    job = engine.create_job(
        JobType.LENS_FITTING, "order-001", "BV/ORD/001",
        "cust-001", "Rajesh Kumar", "9876543210",
        "store-001", "BV-BKR",
        frame_barcode="RB5154-001", prescription_id="rx-001",
        priority=JobPriority.EXPRESS
    )
    print(f"  Job: {job.job_number}")
    print(f"  Expected: {job.expected_date}")
    print(f"  Status: {job.status.value}")
    
    # Order lens
    print("\n📦 Order Lens")
    success, msg, lens = engine.order_lens(
        job.id, "vendor-001", "Essilor Lab",
        "Progressive", "Varilux", "Crizal",
        Decimal("-2.50"), Decimal("-0.75"), 90, Decimal("1.50"),
        Decimal("-2.25"), Decimal("-0.50"), 85, Decimal("1.50")
    )
    print(f"  {msg}")
    
    # Receive lens
    print("\n📥 Receive Lens")
    success, msg = engine.receive_lens(lens.id)
    print(f"  {msg}")
    print(f"  Status: {job.status.value}")
    
    # Assign and start
    print("\n👷 Assign & Start Job")
    engine.assign_job(job.id, "staff-001", "Ramesh")
    success, msg = engine.start_job(job.id)
    print(f"  {msg}")
    
    # Complete
    print("\n✅ Complete Job")
    success, msg = engine.complete_job(job.id)
    print(f"  {msg}")
    
    # QC
    print("\n🔍 Quality Check")
    success, msg = engine.perform_qc(job.id, "qc-001", True, True, True, True, True)
    print(f"  {msg}")
    
    # Deliver
    print("\n📤 Deliver")
    success, msg = engine.deliver_job(job.id)
    print(f"  {msg}")
    
    # Stats
    print("\n📊 Workshop Stats")
    stats = engine.get_job_stats("store-001")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    
    # Status history
    print("\n📜 Status History")
    for entry in job.status_history:
        print(f"  {entry['status']}: {entry['note']}")
    
    print("\n" + "=" * 60)


if __name__ == "__main__":
    demo_workshop()
