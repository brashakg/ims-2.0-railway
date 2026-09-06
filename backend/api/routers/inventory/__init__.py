"""
IMS 2.0 - Inventory Router
===========================
Stock management, stock count/audit, aging analysis, barcode operations

Wave 5 split: this was one 5,893-line module. It is now a package whose
sub-modules register their routes on the SAME `router`, in the SAME order, so
every path, method, dependency and status code is unchanged. The import order
below IS the route-registration order -- FastAPI resolves routes in
registration order, so a parametric path imported early would shadow the
specific paths below it. Do not reorder.
"""

import sys as _sys
import types as _types

from . import _shared
from . import models
from . import helpers
from . import stock
from . import barcode_trace
from . import movements
from . import stock_lookups
from . import opening_stock
from . import aging
from . import stock_count
from . import stock_count_items
from . import stock_count_reconcile
from . import accountability
from . import transfer_stubs
from . import non_moving
from . import scan
from . import contact_lenses
from . import power_grids
from . import analytics
from . import alerts
from . import serials
from . import quarantine
from ._shared import router


_SUBMODULES = (
    _shared,
    models,
    helpers,
    stock,
    barcode_trace,
    movements,
    stock_lookups,
    opening_stock,
    aging,
    stock_count,
    stock_count_items,
    stock_count_reconcile,
    accountability,
    transfer_stubs,
    non_moving,
    scan,
    contact_lenses,
    power_grids,
    analytics,
    alerts,
    serials,
    quarantine,
)

# The flat module was ONE namespace: `from api.routers.inventory import X` and
# `monkeypatch.setattr(inventory, "X", ...)` reached every user of X. Mirror
# every sub-module name onto the package so both keep working. Later
# sub-modules win, exactly as a later statement won in the flat file.
for _mod in _SUBMODULES:
    for _key, _value in vars(_mod).items():
        if not _key.startswith("__"):
            globals()[_key] = _value


class _InventoryPackage(_types.ModuleType):
    """Forwards attribute writes on the package into the sub-modules.

    Patching `api.routers.inventory._get_db` used to rebind the one global every
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


_sys.modules[__name__].__class__ = _InventoryPackage
