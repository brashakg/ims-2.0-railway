"""
IMS 2.0 - Clinical & Optometry Engine
=====================================
Features:
1. Eye test workflow
2. Prescription management
3. Optometrist assignment
4. Prescription validity
5. Lens recommendations
6. Clinical reports
"""
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from decimal import Decimal
from enum import Enum
from typing import List, Optional, Dict, Tuple
import uuid

class PrescriptionSource(Enum):
    TESTED_AT_STORE = "TESTED_AT_STORE"
    FROM_DOCTOR = "FROM_DOCTOR"

class PrescriptionStatus(Enum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"

class LensType(Enum):
    SINGLE_VISION = "SINGLE_VISION"
    BIFOCAL = "BIFOCAL"
    PROGRESSIVE = "PROGRESSIVE"
    READING = "READING"
    CONTACT = "CONTACT"

@dataclass
class EyePower:
    """Power values for one eye"""
    sph: Decimal = Decimal("0")  # Sphere: -20.00 to +20.00
    cyl: Decimal = Decimal("0")  # Cylinder: -6.00 to +6.00
    axis: int = 0                # Axis: 1-180 (whole number)
    add: Decimal = Decimal("0")  # Addition: +0.75 to +3.50
    prism: Decimal = Decimal("0")
    base: Optional[str] = None   # In, Out, Up, Down
    va: Optional[str] = None     # Visual acuity: 6/6, 6/9, etc.
    
    def validate(self) -> Tuple[bool, str]:
        """Validate eye power values"""
        if not -20 <= float(self.sph) <= 20:
            return False, "SPH must be between -20.00 and +20.00"
        if not -6 <= float(self.cyl) <= 6:
            return False, "CYL must be between -6.00 and +6.00"
        if self.cyl != 0 and not (1 <= self.axis <= 180):
            return False, "AXIS must be whole number 1-180"
        if self.add != 0 and not (0.75 <= float(self.add) <= 3.50):
            return False, "ADD must be between +0.75 and +3.50"
        return True, "Valid"

@dataclass
class Prescription:
    id: str
    prescription_number: str
    
    # Patient
    patient_id: str
    patient_name: str
    customer_id: str
    customer_name: str
    
    # Store & Optometrist
    store_id: str
    source: PrescriptionSource
    optometrist_id: Optional[str] = None
    optometrist_name: Optional[str] = None
    doctor_name: Optional[str] = None  # If from external doctor
    
    # Eye powers
    right_eye: EyePower = field(default_factory=EyePower)
    left_eye: EyePower = field(default_factory=EyePower)
    
    # Additional measurements
    pd_distance: Optional[Decimal] = None  # Pupillary distance (distance)
    pd_near: Optional[Decimal] = None      # Pupillary distance (near)
    
    # Validity
    created_at: datetime = field(default_factory=datetime.now)
    valid_until: Optional[date] = None
    validity_months: int = 12  # Default 1 year
    status: PrescriptionStatus = PrescriptionStatus.ACTIVE
    
    # Remarks
    remarks: str = ""
    
    # Recommendations
    recommended_lens_type: Optional[LensType] = None
    recommended_coatings: List[str] = field(default_factory=list)
    
    def set_validity(self, months: int):
        self.validity_months = months
        self.valid_until = (datetime.now() + timedelta(days=30*months)).date()
    
    def is_valid(self) -> bool:
        if self.status != PrescriptionStatus.ACTIVE:
            return False
        if self.valid_until and date.today() > self.valid_until:
            self.status = PrescriptionStatus.EXPIRED
            return False
        return True

@dataclass
class EyeTest:
    """Eye test session"""
    id: str
    test_number: str
    test_date: date
    test_time: datetime
    
    # Patient
    patient_id: str
    patient_name: str
    customer_id: str
    
    # Store & Staff
    store_id: str
    optometrist_id: str
    optometrist_name: str
    
    # Test data
    auto_refraction_r: Optional[str] = None
    auto_refraction_l: Optional[str] = None
    retinoscopy_r: Optional[str] = None
    retinoscopy_l: Optional[str] = None
    
    # Final prescription
    prescription_id: Optional[str] = None
    
    # Status
    status: str = "IN_PROGRESS"  # IN_PROGRESS, COMPLETED, CANCELLED
    
    # Notes
    chief_complaint: str = ""
    clinical_notes: str = ""
    
    duration_minutes: int = 0
    
    created_at: datetime = field(default_factory=datetime.now)

@dataclass
class LensRecommendation:
    lens_type: LensType
    index: str  # 1.50, 1.56, 1.60, 1.67, 1.74
    coating: str
    reason: str
    priority: int = 1

class ClinicalEngine:
    def __init__(self):
        self.prescriptions: Dict[str, Prescription] = {}
        self.eye_tests: Dict[str, EyeTest] = {}
        self._rx_counter = 0
        self._test_counter = 0
    
    def _gen_rx_number(self, store_code: str) -> str:
        self._rx_counter += 1
        return f"RX-{store_code}-{date.today().strftime('%Y%m%d')}-{self._rx_counter:04d}"
    
    def _gen_test_number(self, store_code: str) -> str:
        self._test_counter += 1
        return f"ET-{store_code}-{date.today().strftime('%Y%m%d')}-{self._test_counter:04d}"
    
    def start_eye_test(
        self, patient_id: str, patient_name: str, customer_id: str,
        store_id: str, store_code: str, optom_id: str, optom_name: str,
        chief_complaint: str = ""
    ) -> EyeTest:
        """Start a new eye test session"""
        test = EyeTest(
            id=str(uuid.uuid4()),
            test_number=self._gen_test_number(store_code),
            test_date=date.today(),
            test_time=datetime.now(),
            patient_id=patient_id,
            patient_name=patient_name,
            customer_id=customer_id,
            store_id=store_id,
            optometrist_id=optom_id,
            optometrist_name=optom_name,
            chief_complaint=chief_complaint
        )
        self.eye_tests[test.id] = test
        return test
    
    def complete_eye_test(
        self, test_id: str, right_eye: EyePower, left_eye: EyePower,
        pd_distance: Optional[Decimal] = None, pd_near: Optional[Decimal] = None,
        remarks: str = "", validity_months: int = 12
    ) -> Tuple[bool, str, Optional[Prescription]]:
        """Complete eye test and create prescription"""
        test = self.eye_tests.get(test_id)
        if not test:
            return False, "Eye test not found", None
        
        # Validate powers
        valid, msg = right_eye.validate()
        if not valid:
            return False, f"Right eye: {msg}", None
        valid, msg = left_eye.validate()
        if not valid:
            return False, f"Left eye: {msg}", None
        
        # Create prescription
        rx = Prescription(
            id=str(uuid.uuid4()),
            prescription_number=self._gen_rx_number(test.store_id[:6]),
            patient_id=test.patient_id,
            patient_name=test.patient_name,
            customer_id=test.customer_id,
            customer_name="",  # Would come from customer master
            store_id=test.store_id,
            source=PrescriptionSource.TESTED_AT_STORE,
            optometrist_id=test.optometrist_id,
            optometrist_name=test.optometrist_name,
            right_eye=right_eye,
            left_eye=left_eye,
            pd_distance=pd_distance,
            pd_near=pd_near,
            remarks=remarks
        )
        rx.set_validity(validity_months)
        
        # Recommend lens type
        rx.recommended_lens_type = self._recommend_lens_type(right_eye, left_eye)
        rx.recommended_coatings = self._recommend_coatings(right_eye, left_eye)
        
        self.prescriptions[rx.id] = rx
        
        # Update test
        test.prescription_id = rx.id
        test.status = "COMPLETED"
        test.duration_minutes = int((datetime.now() - test.test_time).total_seconds() / 60)
        
        return True, f"Prescription {rx.prescription_number} created", rx
    
    def create_external_prescription(
        self, patient_id: str, patient_name: str, customer_id: str, customer_name: str,
        store_id: str, store_code: str, doctor_name: str,
        right_eye: EyePower, left_eye: EyePower,
        pd_distance: Optional[Decimal] = None, remarks: str = "", validity_months: int = 6
    ) -> Tuple[bool, str, Optional[Prescription]]:
        """Record prescription from external doctor"""
        
        valid, msg = right_eye.validate()
        if not valid:
            return False, f"Right eye: {msg}", None
        valid, msg = left_eye.validate()
        if not valid:
            return False, f"Left eye: {msg}", None
        
        rx = Prescription(
            id=str(uuid.uuid4()),
            prescription_number=self._gen_rx_number(store_code),
            patient_id=patient_id,
            patient_name=patient_name,
            customer_id=customer_id,
            customer_name=customer_name,
            store_id=store_id,
            source=PrescriptionSource.FROM_DOCTOR,
            doctor_name=doctor_name,
            right_eye=right_eye,
            left_eye=left_eye,
            pd_distance=pd_distance,
            remarks=remarks
        )
        rx.set_validity(validity_months)
        rx.recommended_lens_type = self._recommend_lens_type(right_eye, left_eye)
        
        self.prescriptions[rx.id] = rx
        return True, f"Prescription {rx.prescription_number} recorded", rx
    
    def _recommend_lens_type(self, right: EyePower, left: EyePower) -> LensType:
        """Recommend lens type based on prescription"""
        has_add = right.add > 0 or left.add > 0
        high_cyl = abs(float(right.cyl)) > 2 or abs(float(left.cyl)) > 2
        
        if has_add:
            if high_cyl:
                return LensType.PROGRESSIVE
            # Check patient age (would need patient data)
            return LensType.PROGRESSIVE
        return LensType.SINGLE_VISION
    
    def _recommend_coatings(self, right: EyePower, left: EyePower) -> List[str]:
        """Recommend coatings based on prescription"""
        coatings = ["Anti-Reflective"]
        
        high_power = max(abs(float(right.sph)), abs(float(left.sph))) > 4
        if high_power:
            coatings.append("Thin Lens (High Index)")
        
        # Default recommendations
        coatings.append("Blue Cut")  # For digital use
        coatings.append("Hard Coat")
        
        return coatings
    
    def get_lens_recommendations(self, rx_id: str) -> List[LensRecommendation]:
        """Get detailed lens recommendations for a prescription"""
        rx = self.prescriptions.get(rx_id)
        if not rx:
            return []
        
        recommendations = []
        max_power = max(abs(float(rx.right_eye.sph)), abs(float(rx.left_eye.sph)))
        
        # Index recommendation based on power
        if max_power <= 2:
            index = "1.50"
            reason = "Standard index sufficient for low power"
        elif max_power <= 4:
            index = "1.56"
            reason = "Mid-index for moderate power"
        elif max_power <= 6:
            index = "1.60"
            reason = "High index recommended"
        elif max_power <= 8:
            index = "1.67"
            reason = "Very high index for strong prescription"
        else:
            index = "1.74"
            reason = "Ultra-thin for very strong prescription"
        
        lens_type = rx.recommended_lens_type or LensType.SINGLE_VISION
        
        # Premium option
        recommendations.append(LensRecommendation(
            lens_type=lens_type,
            index=index,
            coating="Blue Cut + Anti-Reflective + Hard Coat",
            reason=reason + " with premium coatings for digital protection",
            priority=1
        ))
        
        # Budget option
        recommendations.append(LensRecommendation(
            lens_type=lens_type,
            index="1.50" if max_power <= 4 else "1.56",
            coating="Anti-Reflective + Hard Coat",
            reason="Budget-friendly option with essential coatings",
            priority=2
        ))
        
        return recommendations
    
    def get_patient_prescriptions(self, patient_id: str) -> List[Prescription]:
        """Get all prescriptions for a patient"""
        return sorted(
            [rx for rx in self.prescriptions.values() if rx.patient_id == patient_id],
            key=lambda x: x.created_at,
            reverse=True
        )
    
    def get_optometrist_stats(self, optom_id: str, month: int, year: int) -> dict:
        """Get optometrist performance stats"""
        tests = [t for t in self.eye_tests.values() 
                 if t.optometrist_id == optom_id 
                 and t.test_date.month == month 
                 and t.test_date.year == year]
        
        completed = [t for t in tests if t.status == "COMPLETED"]
        
        return {
            "total_tests": len(tests),
            "completed_tests": len(completed),
            "average_duration": sum(t.duration_minutes for t in completed) / len(completed) if completed else 0,
            "conversion_rate": len(completed) / len(tests) * 100 if tests else 0
        }

def demo_clinical():
    print("=" * 60)
    print("IMS 2.0 CLINICAL ENGINE DEMO")
    print("=" * 60)
    
    engine = ClinicalEngine()
    
    print("\n👁️ Start Eye Test")
    test = engine.start_eye_test(
        patient_id="pat-001", patient_name="Rajesh Kumar",
        customer_id="cust-001", store_id="store-bv-001", store_code="BV-BKR",
        optom_id="optom-001", optom_name="Dr. Priya Singh",
        chief_complaint="Difficulty reading and headache"
    )
    print(f"  Test: {test.test_number}")
    print(f"  Optometrist: {test.optometrist_name}")
    
    print("\n✅ Complete Eye Test")
    right = EyePower(sph=Decimal("-2.50"), cyl=Decimal("-0.75"), axis=90, add=Decimal("1.50"))
    left = EyePower(sph=Decimal("-2.25"), cyl=Decimal("-0.50"), axis=85, add=Decimal("1.50"))
    
    success, msg, rx = engine.complete_eye_test(
        test_id=test.id, right_eye=right, left_eye=left,
        pd_distance=Decimal("64"), remarks="Progressive recommended", validity_months=12
    )
    print(f"  {msg}")
    print(f"  Rx Number: {rx.prescription_number}")
    print(f"  Valid Until: {rx.valid_until}")
    print(f"  Recommended: {rx.recommended_lens_type.value}")
    print(f"  Coatings: {', '.join(rx.recommended_coatings)}")
    
    print("\n📋 Prescription Details")
    print(f"  RIGHT: SPH {rx.right_eye.sph:+.2f} CYL {rx.right_eye.cyl:+.2f} AXIS {rx.right_eye.axis} ADD {rx.right_eye.add:+.2f}")
    print(f"  LEFT:  SPH {rx.left_eye.sph:+.2f} CYL {rx.left_eye.cyl:+.2f} AXIS {rx.left_eye.axis} ADD {rx.left_eye.add:+.2f}")
    print(f"  PD: {rx.pd_distance}mm")
    
    print("\n💡 Lens Recommendations")
    recs = engine.get_lens_recommendations(rx.id)
    for i, rec in enumerate(recs, 1):
        print(f"  Option {i}: {rec.lens_type.value} {rec.index} index")
        print(f"    Coating: {rec.coating}")
        print(f"    Reason: {rec.reason}")
    
    print("\n📊 External Prescription")
    success, msg, ext_rx = engine.create_external_prescription(
        patient_id="pat-002", patient_name="Sita Devi",
        customer_id="cust-002", customer_name="Ram Kumar",
        store_id="store-bv-001", store_code="BV-BKR",
        doctor_name="Dr. Sharma (City Hospital)",
        right_eye=EyePower(sph=Decimal("-1.00")),
        left_eye=EyePower(sph=Decimal("-1.25")),
        remarks="From external ophthalmologist",
        validity_months=6  # Shorter validity for external
    )
    print(f"  {msg}")
    print(f"  Source: {ext_rx.source.value}")
    print(f"  Doctor: {ext_rx.doctor_name}")
    
    print("\n📈 Optometrist Stats")
    stats = engine.get_optometrist_stats("optom-001", date.today().month, date.today().year)
    print(f"  Tests: {stats['total_tests']}")
    print(f"  Completed: {stats['completed_tests']}")
    
    print("\n" + "=" * 60)

if __name__ == "__main__":
    demo_clinical()
