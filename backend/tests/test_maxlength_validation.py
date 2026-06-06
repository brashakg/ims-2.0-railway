"""
Test suite for max_length validation across create models.
Ensures free-text fields reject unbounded/oversized input (DoS prevention).
"""
import pytest
from datetime import datetime, date
from pydantic import ValidationError
from api.routers.customers import CustomerCreate, PatientCreate
from api.routers.tasks import TaskCreate, TaskComplete, SopStep, SopTemplateCreate


class TestCustomerCreateMaxLength:
    """CustomerCreate.name must respect max_length=120."""

    def test_customer_name_within_limit(self):
        """Valid: name at max_length boundary."""
        c = CustomerCreate(
            name="A" * 120,
            mobile="9876543210",
            customer_type="B2C"
        )
        assert len(c.name) == 120

    def test_customer_name_exceeds_limit(self):
        """Invalid: name exceeds max_length."""
        with pytest.raises(ValidationError) as exc_info:
            CustomerCreate(
                name="A" * 121,
                mobile="9876543210",
                customer_type="B2C"
            )
        assert "at most 120 characters" in str(exc_info.value).lower()

    def test_customer_name_empty_rejected(self):
        """Invalid: name below min_length."""
        with pytest.raises(ValidationError):
            CustomerCreate(
                name="A",  # min_length=2
                mobile="9876543210",
                customer_type="B2C"
            )


class TestPatientCreateMaxLength:
    """PatientCreate.name must respect max_length=120."""

    def test_patient_name_within_limit(self):
        """Valid: patient name at max_length."""
        p = PatientCreate(name="B" * 120)
        assert len(p.name) == 120

    def test_patient_name_exceeds_limit(self):
        """Invalid: patient name exceeds max_length."""
        with pytest.raises(ValidationError) as exc_info:
            PatientCreate(name="B" * 121)
        assert "at most 120 characters" in str(exc_info.value).lower()

    def test_patient_name_minimum_respected(self):
        """Valid: patient name with single character (no min_length in original)."""
        p = PatientCreate(name="X")
        assert p.name == "X"


class TestTaskCreateMaxLength:
    """TaskCreate enforces max_length on title, description, category, assigned_to."""

    def test_task_title_within_limit(self):
        """Valid: title at max_length=500."""
        t = TaskCreate(
            title="T" * 500,
            assigned_to="user123"
        )
        assert len(t.title) == 500

    def test_task_title_exceeds_limit(self):
        """Invalid: title exceeds max_length."""
        with pytest.raises(ValidationError) as exc_info:
            TaskCreate(
                title="T" * 501,
                assigned_to="user123"
            )
        assert "at most 500 characters" in str(exc_info.value).lower()

    def test_task_description_within_limit(self):
        """Valid: description at max_length=2000."""
        t = TaskCreate(
            title="Task",
            description="D" * 2000,
            assigned_to="user123"
        )
        assert len(t.description) == 2000

    def test_task_description_exceeds_limit(self):
        """Invalid: description exceeds max_length."""
        with pytest.raises(ValidationError) as exc_info:
            TaskCreate(
                title="Task",
                description="D" * 2001,
                assigned_to="user123"
            )
        assert "at most 2000 characters" in str(exc_info.value).lower()

    def test_task_category_within_limit(self):
        """Valid: category at max_length=100."""
        t = TaskCreate(
            title="Task",
            category="C" * 100,
            assigned_to="user123"
        )
        assert len(t.category) == 100

    def test_task_category_exceeds_limit(self):
        """Invalid: category exceeds max_length."""
        with pytest.raises(ValidationError) as exc_info:
            TaskCreate(
                title="Task",
                category="C" * 101,
                assigned_to="user123"
            )
        assert "at most 100 characters" in str(exc_info.value).lower()

    def test_task_assigned_to_within_limit(self):
        """Valid: assigned_to at max_length=255."""
        t = TaskCreate(
            title="Task",
            assigned_to="U" * 255
        )
        assert len(t.assigned_to) == 255

    def test_task_assigned_to_exceeds_limit(self):
        """Invalid: assigned_to exceeds max_length."""
        with pytest.raises(ValidationError) as exc_info:
            TaskCreate(
                title="Task",
                assigned_to="U" * 256
            )
        assert "at most 255 characters" in str(exc_info.value).lower()


class TestTaskCompleteMaxLength:
    """TaskComplete.completion_notes enforces max_length=2000."""

    def test_completion_notes_within_limit(self):
        """Valid: completion_notes at max_length=2000."""
        tc = TaskComplete(completion_notes="N" * 2000)
        assert len(tc.completion_notes) == 2000

    def test_completion_notes_exceeds_limit(self):
        """Invalid: completion_notes exceeds max_length."""
        with pytest.raises(ValidationError) as exc_info:
            TaskComplete(completion_notes="N" * 2001)
        assert "at most 2000 characters" in str(exc_info.value).lower()

    def test_completion_notes_below_min(self):
        """Invalid: completion_notes below min_length=3."""
        with pytest.raises(ValidationError):
            TaskComplete(completion_notes="AB")


class TestSopStepMaxLength:
    """SopStep enforces max_length on instruction and warning."""

    def test_sop_instruction_within_limit(self):
        """Valid: instruction at max_length=1000."""
        s = SopStep(step_number=1, instruction="I" * 1000)
        assert len(s.instruction) == 1000

    def test_sop_instruction_exceeds_limit(self):
        """Invalid: instruction exceeds max_length."""
        with pytest.raises(ValidationError) as exc_info:
            SopStep(step_number=1, instruction="I" * 1001)
        assert "at most 1000 characters" in str(exc_info.value).lower()

    def test_sop_warning_within_limit(self):
        """Valid: warning at max_length=500."""
        s = SopStep(step_number=1, instruction="Do X", warning="W" * 500)
        assert len(s.warning) == 500

    def test_sop_warning_exceeds_limit(self):
        """Invalid: warning exceeds max_length."""
        with pytest.raises(ValidationError) as exc_info:
            SopStep(step_number=1, instruction="Do X", warning="W" * 501)
        assert "at most 500 characters" in str(exc_info.value).lower()


class TestSopTemplateCreateMaxLength:
    """SopTemplateCreate enforces max_length on title and description."""

    def test_sop_template_title_within_limit(self):
        """Valid: SOP title at max_length=500."""
        s = SopTemplateCreate(
            title="T" * 500,
            category="Operations",
            frequency="DAILY"
        )
        assert len(s.title) == 500

    def test_sop_template_title_exceeds_limit(self):
        """Invalid: SOP title exceeds max_length."""
        with pytest.raises(ValidationError) as exc_info:
            SopTemplateCreate(
                title="T" * 501,
                category="Operations",
                frequency="DAILY"
            )
        assert "at most 500 characters" in str(exc_info.value).lower()

    def test_sop_template_description_within_limit(self):
        """Valid: SOP description at max_length=2000."""
        s = SopTemplateCreate(
            title="Template",
            description="D" * 2000,
            category="Operations"
        )
        assert len(s.description) == 2000

    def test_sop_template_description_exceeds_limit(self):
        """Invalid: SOP description exceeds max_length."""
        with pytest.raises(ValidationError) as exc_info:
            SopTemplateCreate(
                title="Template",
                description="D" * 2001,
                category="Operations"
            )
        assert "at most 2000 characters" in str(exc_info.value).lower()
