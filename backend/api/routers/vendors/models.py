"""Request models for vendors, purchase orders and goods receipts."""

from ._shared import (
    BaseModel,
    Field,
    List,
    Optional,
    field_validator,
    model_validator,
    validate_vendor_gstin,
)


class VendorCreate(BaseModel):
    legal_name: str
    trade_name: str
    vendor_type: str = "INDIAN"
    gstin_status: str
    # GSTIN must match the 15-character Indian format when the vendor is
    # REGISTERED. An UNREGISTERED / COMPOSITION / OVERSEAS vendor may omit it.
    gstin: Optional[str] = None
    address: str
    city: str
    state: str
    mobile: str
    email: Optional[str] = None
    # The supplier's own code + the person you actually ring. Both are asked
    # for on the Add-Supplier form; before this they were collected and thrown
    # away (the router never wrote them), so they came back blank on reload.
    vendor_code: Optional[str] = None
    contact_person: Optional[str] = None
    credit_limit: Optional[float] = Field(default=None, ge=0)
    # Credit terms must be non-negative. 0 = COD (immediate payment), which is
    # legitimate; negative days would produce a due date BEFORE the bill date
    # and poison the AP aging calculation.
    credit_days: int = Field(30, ge=0)

    @field_validator("gstin", mode="before")
    @classmethod
    def _validate_gstin(cls, v):
        return validate_vendor_gstin(v)


class VendorUpdate(BaseModel):
    legal_name: Optional[str] = None
    trade_name: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    mobile: Optional[str] = None
    email: Optional[str] = None
    vendor_code: Optional[str] = None
    contact_person: Optional[str] = None
    credit_limit: Optional[float] = Field(default=None, ge=0)
    # A wrong GSTIN was previously uncorrectable -- the field simply wasn't on
    # the update model -- so a vendor typo'd at create time stayed mis-taxed
    # forever. Validated by the same rule as create, and only looked at when
    # actually supplied (editing a phone number never re-judges the GSTIN).
    gstin: Optional[str] = None
    gstin_status: Optional[str] = None
    # credit_days must be non-negative on update too.
    credit_days: Optional[int] = Field(default=None, ge=0)
    is_active: Optional[bool] = None

    @field_validator("gstin", mode="before")
    @classmethod
    def _validate_gstin(cls, v):
        return validate_vendor_gstin(v)


class POItemNewProduct(BaseModel):
    """The identity a buyer types for an item that is not catalogued YET.

    Owner ruling 13: you order from a vendor's list before the product exists in
    IMS, so the system must stop being the obstacle at the FRONT of the flow.
    These are exactly the fields he named -- brand, model no, colour code, size
    and MRP. The line's own `unit_price` is the cost price (provisional: the
    purchase INVOICE later corrects it to the actual one, ruling 12).

    There is deliberately NO selling price here: see product_master's provisional
    door. Without one the product can never stamp ACTIVE, so it can never be
    sold before a cataloguer finishes it.
    """

    # FRAME is the overwhelmingly common case at the buy desk; any registry
    # category is accepted and resolved server-side.
    category: str = "FRAME"
    brand: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    colour: Optional[str] = None
    size: Optional[str] = None
    mrp: float = Field(..., gt=0)


class POItemCreate(BaseModel):
    # Ruling 13: a line references EITHER an existing catalogued product OR
    # carries the identity of one that does not exist yet, which the server
    # materialises into a real (provisional, unsellable) spine row before the PO
    # is written. product_id stays the join key for everything downstream --
    # receiving, the stock mint, the invoice and the 3-way match all key on it.
    product_id: Optional[str] = None
    product_name: Optional[str] = None
    sku: Optional[str] = None
    new_product: Optional[POItemNewProduct] = None
    # A PO line must order at least one unit at a non-negative price. Without
    # these bounds a negative quantity / price would persist a corrupt PO and
    # poison the subtotal/GST math (subtotal = sum(quantity * unit_price)).
    quantity: int = Field(..., ge=1)
    unit_price: float = Field(..., ge=0)
    # Per-line GST identity. All optional: when gst_rate is None the server
    # resolves it from hsn/category (falling back to the product's own
    # hsn/category), so a PO never silently bills the old flat 18%.
    hsn: Optional[str] = None
    gst_rate: Optional[float] = Field(default=None, ge=0, le=100)
    category: Optional[str] = None

    @model_validator(mode="after")
    def _one_product_identity(self):
        """A line names an existing product OR describes a new one -- never both,
        never neither. Both would be ambiguous about which identity the receipt
        and the bill are settled against."""
        has_id = bool((self.product_id or "").strip())
        has_new = self.new_product is not None
        if has_id and has_new:
            raise ValueError(
                "a PO line cannot both reference an existing product and "
                "describe a new one"
            )
        if not has_id and not has_new:
            raise ValueError(
                "a PO line needs either a catalogued product or the new item's "
                "brand, model, colour, size and MRP"
            )
        if has_id and not (self.product_name or "").strip():
            raise ValueError("product_name is required for a catalogued line")
        return self


class POCreate(BaseModel):
    vendor_id: str
    delivery_store_id: str
    # An empty items list would store a PO with subtotal=0/tax=0/total=0 --
    # a corrupt record that passes all downstream checks but means nothing.
    # Enforce at least one line item.
    items: List[POItemCreate] = Field(..., min_length=1)
    expected_date: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("expected_date")
    @classmethod
    def _expected_date_not_backdated(cls, v):
        """Owner rule: a promised delivery is today or later, never the past.

        Enforced HERE, on the server, not only by the date picker's `min` --
        a picker minimum is a courtesy, not a rule, and any other caller
        (Buy Desk, a script, a replayed request) bypasses it entirely.

        "Today" is the IST calendar day: Railway runs in UTC, so between
        00:00 and 05:30 IST a UTC "today" is still YESTERDAY in the shop and
        would reject a perfectly valid same-day delivery date.

        Applies to CREATE only -- POs already carrying an older date keep it
        and still open, display and receive exactly as before.
        """
        if v is None:
            return v
        raw = str(v).strip()
        if not raw:
            return v
        from datetime import date as _date
        from ...utils.ist import ist_today

        try:
            parsed = _date.fromisoformat(raw[:10])
        except ValueError:
            raise ValueError(
                "Expected delivery date must be a real date, like 2026-08-26"
            )
        if parsed < ist_today():
            raise ValueError(
                "Expected delivery date cannot be in the past - "
                "choose today or a later date"
            )
        return raw[:10]


class GRNItemCreate(BaseModel):
    # F9: optional -- a no-PO Delivery Challan line has no PO item to reference.
    # A standard GRN line still carries it (the frontend always sends it).
    po_item_id: Optional[str] = None
    product_id: str
    # Receipt quantities are counts of physical units -- never negative. A
    # negative received/accepted/rejected qty would mint a negative stock
    # movement and corrupt the PO receipt-state + accepted-qty rollups.
    received_qty: int = Field(..., ge=0)
    accepted_qty: int = Field(..., ge=0)
    rejected_qty: int = Field(0, ge=0)
    rejection_reason: Optional[str] = None
    # Ruling 14 -- THE TALLY TICK. The receiver ticks each line to say "I have
    # counted this one against what was ordered". Defaults to False so the tick
    # is a real act: a clerk who touches nothing can no longer post a perfect
    # receipt off the pre-filled ordered quantity. Enforced for PO-backed
    # STANDARD receipts only (a Delivery Challan has no order to tally against).
    tallied: bool = False
    # Receiving location for the minted serialized units (optional; falls back
    # to "DEFAULT" on the stock unit). Lets the receiver bin goods at post time.
    location_code: Optional[str] = None
    # P2 (optical batch/expiry): a contact-lens line carries the supplier batch
    # + expiry so each minted unit is dated for FEFO consumption + near-expiry
    # reporting (the stock_unit model + FEFO helpers already key on these).
    # Optional + backward-compatible: a frame/spectacle line simply omits them.
    # lot_number is an accepted alias for batch_code (CL convention).
    batch_code: Optional[str] = None
    lot_number: Optional[str] = None
    expiry_date: Optional[str] = None

    @field_validator("batch_code", "lot_number", "expiry_date", mode="before")
    @classmethod
    def _blank_to_none(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    @model_validator(mode="after")
    def _validate_qty_coherence(self):
        """Cross-field quantity guard.

        Two physical invariants that Pydantic field bounds alone cannot enforce:

        1. accepted_qty <= received_qty -- you cannot accept more units than
           arrived. An accepted_qty > received_qty would produce a positive
           stock write larger than the physical receipt and corrupt inventory.

        2. accepted_qty + rejected_qty == received_qty -- every received unit
           must be either accepted (added to stock) or rejected (returned /
           debit-noted). A mismatch silently discards or double-counts units.
        """
        rec = self.received_qty
        acc = self.accepted_qty
        rej = self.rejected_qty
        if acc > rec:
            raise ValueError(f"accepted_qty ({acc}) cannot exceed received_qty ({rec})")
        if acc + rej != rec:
            raise ValueError(
                f"accepted_qty ({acc}) + rejected_qty ({rej}) must equal "
                f"received_qty ({rec})"
            )
        return self


# F9: GRN subtypes. A STANDARD GRN is received against a PO + the vendor's tax
# invoice (vendor_invoice_no is mandatory). A DELIVERY_CHALLAN (DC) is the
# physical goods-receipt doc a lens lab sends WITH external-lab lenses -- the tax
# invoice comes later (monthly/fortnightly), so vendor_invoice_no is optional and
# the DC carries its own dc_number + dc_date. A missing grn_subtype on legacy
# docs reads as STANDARD (backward-compatible).
GRN_SUBTYPE_STANDARD = "STANDARD"
GRN_SUBTYPE_DC = "DELIVERY_CHALLAN"
_GRN_SUBTYPES = (GRN_SUBTYPE_STANDARD, GRN_SUBTYPE_DC)


class GRNCreate(BaseModel):
    # F9: po_id is REQUIRED for a STANDARD GRN but OPTIONAL for a DELIVERY_CHALLAN
    # (a lens top-up DC often arrives with no pre-logged PO). Enforced in the
    # model_validator below so the field default can be None.
    po_id: Optional[str] = None
    # F9: vendor_invoice_no is REQUIRED for a STANDARD GRN but OPTIONAL for a DC
    # (the tax invoice arrives later and is reconciled via the bulk DC->invoice
    # tally). Enforced in the validator.
    vendor_invoice_no: Optional[str] = None
    vendor_invoice_date: Optional[str] = None
    # A GRN with zero items is meaningless and would mark a PO as having
    # been received without actually recording any goods.
    items: List[GRNItemCreate] = Field(..., min_length=1)
    notes: Optional[str] = None
    # F9: Delivery-Challan fields.
    grn_subtype: str = GRN_SUBTYPE_STANDARD
    dc_number: Optional[str] = None
    dc_date: Optional[str] = None
    # F9: the vendor a no-PO DC is for (a STANDARD GRN derives this from the PO).
    vendor_id: Optional[str] = None
    # F-S3: mandatory goods-receipt document. The ops user (Superadmin/Admin/
    # Store Manager) physically receiving the stock MUST attach the vendor
    # invoice/challan image or PDF BEFORE the GRN can be created -- so the
    # accountant always has the source document to reconcile against. The file
    # is uploaded first via POST /vendors/grn/upload-doc, which returns a
    # file_id that is then passed here. STANDARD GRNs require it; a
    # DELIVERY_CHALLAN is exempt at receipt time (its tax invoice arrives later
    # and is attached at reconciliation -- see P3). Gate enforced in create_grn.
    attachment_file_id: Optional[str] = None
    attachment_filename: Optional[str] = None
    attachment_mime: Optional[str] = None

    @field_validator("grn_subtype", mode="before")
    @classmethod
    def _normalize_subtype(cls, v):
        s = str(v or GRN_SUBTYPE_STANDARD).strip().upper().replace("-", "_")
        return s if s in _GRN_SUBTYPES else GRN_SUBTYPE_STANDARD

    @model_validator(mode="after")
    def _validate_subtype_fields(self):
        """F9 subtype-specific required-field guard.

        STANDARD: po_id + vendor_invoice_no are both required (the existing
                  contract -- a standard GRN is always against a PO + invoice).
        DELIVERY_CHALLAN: dc_number + dc_date are required; po_id +
                  vendor_invoice_no are optional (they come later).
        """
        if self.grn_subtype == GRN_SUBTYPE_DC:
            if not (self.dc_number and str(self.dc_number).strip()):
                raise ValueError("dc_number is required for a Delivery Challan")
            if not (self.dc_date and str(self.dc_date).strip()):
                raise ValueError("dc_date is required for a Delivery Challan")
        else:
            if not (self.po_id and str(self.po_id).strip()):
                raise ValueError("po_id is required for a standard GRN")
            if not (self.vendor_invoice_no and str(self.vendor_invoice_no).strip()):
                raise ValueError("vendor_invoice_no is required for a standard GRN")
        return self


class ExpressGRNItemCreate(BaseModel):
    """One received line for the express (clean-delivery) receiving chain.

    Mirrors GRNItemCreate's fields but WITHOUT the cross-field coherence
    validator: express enforces its own stricter CLEAN-ONLY rule in the
    handler (rejected == 0 and accepted == received > 0) and answers every
    violation with ONE stable 400 code (EXPRESS_NOT_CLEAN) the frontend keys
    on to fall back to the two-step receive -- a Pydantic 422 would leak a
    different error shape for some non-clean payloads.
    """

    po_item_id: Optional[str] = None
    product_id: str
    received_qty: int = Field(..., ge=0)
    accepted_qty: int = Field(..., ge=0)
    rejected_qty: int = Field(0, ge=0)
    location_code: Optional[str] = None
    batch_code: Optional[str] = None
    lot_number: Optional[str] = None
    expiry_date: Optional[str] = None

    @field_validator("batch_code", "lot_number", "expiry_date", mode="before")
    @classmethod
    def _blank_to_none(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None


class ExpressGRNCreate(BaseModel):
    """Body for POST /vendors/grn/express -- the one-shot clean receive.

    STANDARD PO-backed receipts only: po_id + vendor_invoice_no are required
    (grn_subtype is accepted for explicitness but a DELIVERY_CHALLAN is
    rejected in the handler with EXPRESS_STANDARD_ONLY). The attachment fields
    flow into the SAME F-S3 mandatory-document gate create_grn enforces --
    attachment_file_id stays Optional here so a missing document surfaces as
    the canonical 400 ATTACHMENT_REQUIRED (not a 422), keeping the
    no-paper-no-stock control's error contract identical across both flows.
    """

    po_id: str = Field(..., min_length=1)
    vendor_invoice_no: str = Field(..., min_length=1)
    vendor_invoice_date: Optional[str] = None
    items: List[ExpressGRNItemCreate] = Field(..., min_length=1)
    attachment_file_id: Optional[str] = None
    attachment_filename: Optional[str] = None
    attachment_mime: Optional[str] = None
    notes: Optional[str] = None
    grn_subtype: str = GRN_SUBTYPE_STANDARD

    @field_validator("grn_subtype", mode="before")
    @classmethod
    def _normalize_subtype(cls, v):
        s = str(v or GRN_SUBTYPE_STANDARD).strip().upper().replace("-", "_")
        return s if s in _GRN_SUBTYPES else GRN_SUBTYPE_STANDARD
