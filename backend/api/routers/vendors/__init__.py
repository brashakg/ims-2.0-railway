"""IMS 2.0 - Vendors Router
=========================
Real database queries for vendor and purchase order management

Wave 5 split: this was one 6,850-line module. It is now a package whose
sub-modules register their routes on the SAME `router`, in the SAME order, so
every path, method, dependency and status code is unchanged. The import order
below IS the route-registration order -- FastAPI resolves routes in
registration order, so a parametric path imported early would shadow the
specific paths below it. Do not reorder.
"""

import sys as _sys
import types as _types

from . import _shared
from . import gst
from . import models
from . import numbering
from . import master
from . import purchase_orders
from . import cockpit
from . import po_detail
from . import grn
from . import grn_create
from . import grn_accept_lock
from . import grn_accept
from . import grn_express
from . import grn_void
from . import portal
from . import ap_bills
from . import ap_payments
from . import performance
from . import tds
from . import variance
from ._shared import router
from .master import get_vendor


# ============================================================================
# Catch-all parametric routes — registered LAST so they do not shadow
# specific paths above (`/purchase-orders`, `/grn`, `/ap-aging`, etc.).
# FastAPI resolves routes in registration order.
# ============================================================================
router.add_api_route("/{vendor_id}", get_vendor, methods=["GET"])


_SUBMODULES = (
    _shared,
    gst,
    models,
    numbering,
    master,
    purchase_orders,
    cockpit,
    po_detail,
    grn,
    grn_create,
    grn_accept_lock,
    grn_accept,
    grn_express,
    grn_void,
    portal,
    ap_bills,
    ap_payments,
    performance,
    tds,
    variance,
)

# The flat module was ONE namespace: `from api.routers.vendors import X` and
# `monkeypatch.setattr(vendors, "X", ...)` reached every user of X. Mirror every
# sub-module name onto the package so both keep working.
for _mod in _SUBMODULES:
    for _key, _value in vars(_mod).items():
        if not _key.startswith("__"):
            globals()[_key] = _value


class _VendorsPackage(_types.ModuleType):
    """Forwards attribute writes on the package into the sub-modules.

    Patching `api.routers.vendors._get_db` used to rebind the one global every
    handler read. After the split a handler reads its OWN module global, so the
    write is forwarded to every sub-module holding that name -- exactly the set
    the flat module's single namespace covered.
    """

    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        for mod in _SUBMODULES:
            if name in mod.__dict__:
                mod.__dict__[name] = value

    def __delattr__(self, name):
        super().__delattr__(name)
        for mod in _SUBMODULES:
            mod.__dict__.pop(name, None)


_sys.modules[__name__].__class__ = _VendorsPackage
