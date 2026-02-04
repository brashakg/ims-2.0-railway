"""
IMS 2.0 - Printables Engine
============================
Document generation for all print requirements
"""
from dataclasses import dataclass, field
from datetime import datetime, date
from decimal import Decimal
from enum import Enum
from typing import List, Optional, Dict
import uuid

class DocumentType(Enum):
    TAX_INVOICE = "TAX_INVOICE"
    PROFORMA = "PROFORMA"
    PURCHASE_ORDER = "PURCHASE_ORDER"
    JOB_CARD = "JOB_CARD"
    EYE_TEST_TOKEN = "EYE_TEST_TOKEN"
    PRESCRIPTION = "PRESCRIPTION"
    BARCODE_LABEL = "BARCODE_LABEL"
    DELIVERY_CHALLAN = "DELIVERY_CHALLAN"

class PrintSize(Enum):
    A4 = "A4"
    A5 = "A5"
    THERMAL_80MM = "THERMAL_80MM"
    BARCODE_38X25 = "BARCODE_38X25"

@dataclass
class CompanyInfo:
    name: str
    gstin: str
    address: str
    city: str
    state: str
    phone: str

@dataclass
class PrintJob:
    id: str
    document_type: DocumentType
    reference_number: str
    store_id: str
    generated_at: datetime = field(default_factory=datetime.now)
    print_count: int = 0

@dataclass
class BarcodeData:
    barcode_value: str
    brand: str
    model: str
    color: str
    mrp: Decimal
    location_code: str

@dataclass
class PrescriptionData:
    rx_number: str
    patient_name: str
    r_sph: str
    r_cyl: str
    r_axis: str
    l_sph: str
    l_cyl: str
    l_axis: str
    optometrist_name: str


class PrintablesEngine:
    def __init__(self):
        self.print_jobs: Dict[str, PrintJob] = {}
        self.company_info: Dict[str, CompanyInfo] = {}
    
    def set_company_info(self, store_id: str, info: CompanyInfo):
        self.company_info[store_id] = info
    
    def _create_job(self, doc_type: DocumentType, ref_num: str, store_id: str) -> PrintJob:
        job = PrintJob(
            id=str(uuid.uuid4()),
            document_type=doc_type,
            reference_number=ref_num,
            store_id=store_id
        )
        self.print_jobs[job.id] = job
        return job
    
    def generate_tax_invoice(self, invoice_number: str, customer_name: str, 
                             items: List[Dict], total: Decimal, store_id: str) -> Dict:
        job = self._create_job(DocumentType.TAX_INVOICE, invoice_number, store_id)
        company = self.company_info.get(store_id)
        
        items_html = ""
        for i, item in enumerate(items, 1):
            items_html += f"<tr><td>{i}</td><td>{item['name']}</td><td>{item['qty']}</td><td>₹{item['rate']}</td><td>₹{item['total']}</td></tr>"
        
        html = f"""
<!DOCTYPE html>
<html>
<head><title>Invoice {invoice_number}</title>
<style>body{{font-family:Arial;margin:20px}}table{{width:100%;border-collapse:collapse}}th,td{{border:1px solid #ccc;padding:8px}}</style>
</head>
<body>
<h2>{company.name if company else 'Store'}</h2>
<p>GSTIN: {company.gstin if company else ''}</p>
<h3>TAX INVOICE: {invoice_number}</h3>
<p>Customer: {customer_name}</p>
<table><tr><th>#</th><th>Item</th><th>Qty</th><th>Rate</th><th>Total</th></tr>{items_html}</table>
<p><strong>Grand Total: ₹{total:,.2f}</strong></p>
</body></html>"""
        return {"job_id": job.id, "html": html}
    
    def generate_barcode_label(self, data: BarcodeData, store_id: str, qty: int = 1) -> Dict:
        job = self._create_job(DocumentType.BARCODE_LABEL, data.barcode_value, store_id)
        
        zpl = f"^XA^FO20,20^A0N,20,20^FD{data.brand}^FS^FO20,45^A0N,18,18^FD{data.model} {data.color}^FS^FO20,70^BY2^BCN,50,Y,N,N^FD{data.barcode_value}^FS^FO20,130^A0N,22,22^FDMRP: Rs.{data.mrp:,.0f}^FS^FO20,155^A0N,16,16^FD{data.location_code}^FS^PQ{qty}^XZ"
        
        html = f"<div style='width:38mm;border:1px solid #000;padding:2mm;font-size:8px'><b>{data.brand}</b><br>{data.model} {data.color}<br>[BARCODE: {data.barcode_value}]<br><b>MRP: ₹{data.mrp:,.0f}</b><br>{data.location_code}</div>"
        return {"job_id": job.id, "zpl": zpl, "html": html, "qty": qty}
    
    def generate_prescription(self, data: PrescriptionData, store_id: str) -> Dict:
        job = self._create_job(DocumentType.PRESCRIPTION, data.rx_number, store_id)
        
        html = f"""
<html><head><title>Rx {data.rx_number}</title></head>
<body style='font-family:Arial;padding:15px'>
<h2>℞ PRESCRIPTION</h2>
<p><b>Rx:</b> {data.rx_number} | <b>Patient:</b> {data.patient_name}</p>
<table border='1' style='border-collapse:collapse;width:100%'>
<tr><th>Eye</th><th>SPH</th><th>CYL</th><th>AXIS</th></tr>
<tr><td>R</td><td>{data.r_sph}</td><td>{data.r_cyl}</td><td>{data.r_axis}</td></tr>
<tr><td>L</td><td>{data.l_sph}</td><td>{data.l_cyl}</td><td>{data.l_axis}</td></tr>
</table>
<p style='margin-top:20px;text-align:right'><b>{data.optometrist_name}</b></p>
</body></html>"""
        return {"job_id": job.id, "html": html}
    
    def generate_job_card(self, job_number: str, customer_name: str, frame: str, 
                          lens: str, r_power: str, l_power: str, store_id: str) -> Dict:
        job = self._create_job(DocumentType.JOB_CARD, job_number, store_id)
        
        html = f"""
<html><body style='font-family:Arial;padding:15px'>
<h2 style='background:#333;color:#fff;padding:10px'>JOB CARD: {job_number}</h2>
<p><b>Customer:</b> {customer_name}</p>
<p><b>Frame:</b> {frame}</p>
<p><b>Lens:</b> {lens}</p>
<p><b>R Power:</b> {r_power}</p>
<p><b>L Power:</b> {l_power}</p>
<hr>
<p>Received By: _______ | Fitted By: _______ | QC By: _______</p>
</body></html>"""
        return {"job_id": job.id, "html": html}
    
    def generate_eye_test_token(self, token_number: str, patient_name: str, 
                                 queue_pos: int, store_id: str) -> Dict:
        job = self._create_job(DocumentType.EYE_TEST_TOKEN, token_number, store_id)
        
        html = f"""
<div style='width:80mm;text-align:center;font-family:Arial;padding:5mm'>
<div style='font-size:14px;font-weight:bold'>EYE TEST TOKEN</div>
<div style='font-size:48px;font-weight:bold;margin:10px'>{token_number}</div>
<div>{patient_name}</div>
<div style='font-size:10px'>Queue: {queue_pos}</div>
<div style='font-size:8px;margin-top:10px'>{datetime.now().strftime('%d/%m/%Y %H:%M')}</div>
</div>"""
        return {"job_id": job.id, "html": html}
    
    def record_print(self, job_id: str) -> bool:
        job = self.print_jobs.get(job_id)
        if job:
            job.print_count += 1
            return True
        return False


def demo_printables():
    print("=" * 60)
    print("IMS 2.0 PRINTABLES ENGINE DEMO")
    print("=" * 60)
    
    engine = PrintablesEngine()
    engine.set_company_info("store-001", CompanyInfo(
        "Better Vision Opticals", "20AABCB1234F1ZP",
        "Main Road", "Bokaro", "Jharkhand", "9876543210"
    ))
    
    print("\n📄 Tax Invoice")
    result = engine.generate_tax_invoice(
        "BV/INV/001", "Rajesh Kumar",
        [{"name": "Ray-Ban Frame", "qty": 1, "rate": 8500, "total": 8500}],
        Decimal("10030"), "store-001"
    )
    print(f"  Generated: {result['job_id'][:8]}")
    
    print("\n🏷️ Barcode Label")
    result = engine.generate_barcode_label(
        BarcodeData("RB5154-BLK", "Ray-Ban", "RB5154", "Black", Decimal("8500"), "C1-S2"),
        "store-001", 2
    )
    print(f"  ZPL: {len(result['zpl'])} chars, Qty: {result['qty']}")
    
    print("\n📋 Prescription")
    result = engine.generate_prescription(
        PrescriptionData("RX-001", "Rajesh Kumar", "-2.50", "-0.75", "90", "-2.25", "-0.50", "85", "Dr. Priya"),
        "store-001"
    )
    print(f"  Generated: {result['job_id'][:8]}")
    
    print("\n🔧 Job Card")
    result = engine.generate_job_card(
        "JOB-001", "Rajesh Kumar", "Ray-Ban RB5154", "Crizal Prevencia",
        "-2.50/-0.75x90", "-2.25/-0.50x85", "store-001"
    )
    print(f"  Generated: {result['job_id'][:8]}")
    
    print("\n🎫 Eye Test Token")
    result = engine.generate_eye_test_token("T-005", "Rajesh Kumar", 3, "store-001")
    print(f"  Generated: {result['job_id'][:8]}")
    
    print("\n" + "=" * 60)


if __name__ == "__main__":
    demo_printables()
