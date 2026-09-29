"""Eye-test history by patient phone and by customer id.

Moved verbatim out of the 3,158-line api/routers/clinical.py (Wave 6
package split): no path, method, dependency, status code, response_model,
default or validation was changed.
"""

from fastapi import Depends
from ..prescriptions import require_rx_read
from ...dependencies import get_eye_test_repository
from ._shared import _filter_tests_by_store_scope, router
from .helpers import _convert_to_camel


@router.get("/tests/patient/{customer_phone}")
async def get_patient_tests(
    customer_phone: str, current_user: dict = Depends(require_rx_read)
):
    """Get all tests for a patient by phone number.

    Phone numbers are trivially enumerable, so this lookup was a P1 medical
    data leak: ANY authenticated role could pull a patient's full eye-test
    history chain-wide. Now role-gated to the prescription-read set
    (require_rx_read) and store-scoped: a store-level caller only sees their
    own store's tests (legacy unattributed docs stay visible -- see
    _filter_tests_by_store_scope).
    """
    test_repo = get_eye_test_repository()

    if test_repo is not None:
        tests = _filter_tests_by_store_scope(
            test_repo.get_patient_tests(customer_phone), current_user
        )
        result = []
        for test in tests:
            converted = _convert_to_camel(test)
            converted["id"] = test.get("test_id")
            result.append(converted)
        return {"tests": result, "total": len(result)}

    return {"tests": [], "total": 0}


@router.get("/tests/customer/{customer_id}")
async def get_customer_tests(
    customer_id: str, current_user: dict = Depends(require_rx_read)
):
    """Get all tests for a customer by ID.

    Same P1 IDOR class as the phone lookup above: role-gated to the
    prescription-read set + store-scoped per object.
    """
    test_repo = get_eye_test_repository()

    if test_repo is not None:
        tests = _filter_tests_by_store_scope(
            test_repo.get_customer_tests(customer_id), current_user
        )
        result = []
        for test in tests:
            converted = _convert_to_camel(test)
            converted["id"] = test.get("test_id")
            result.append(converted)
        return {"tests": result, "total": len(result)}

    return {"tests": [], "total": 0}
