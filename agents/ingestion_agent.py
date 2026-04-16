"""
agents/ingestion_agent.py

Handles all invoice formats: TXT, JSON, CSV (vertical key-value and horizontal tabular),
XML, and PDF. Normalizes item names, invoice numbers, vendor fields, and dates before
passing to the LLM for final structured extraction.

LangGraph 1.1.6 pattern:
- Read only what you need from state
- Return ONLY the keys this agent changes
- LangGraph merges them back into full state automatically
"""
import json
import re
import os
import csv
import io
import xml.etree.ElementTree as ET
from llm_client import call_llm


# ---------------------------------------------------------------------------
# Item name normalization
# Strips internal spaces so "Widget A" -> "WidgetA", "Gadget X" -> "GadgetX"
# ---------------------------------------------------------------------------
ITEM_NAME_MAP = {
    "widget a": "WidgetA",
    "widget b": "WidgetB",
    "gadget x": "GadgetX",
    "fake item": "FakeItem",
}

def normalize_item_name(name: str) -> str:
    cleaned = name.strip()
    lower = cleaned.lower()
    if lower in ITEM_NAME_MAP:
        return ITEM_NAME_MAP[lower]
    # Generic fallback: remove spaces between a word and a single letter/digit
    return re.sub(r'([A-Za-z]+)\s+([A-Za-z0-9])$', lambda m: m.group(1) + m.group(2), cleaned)


def normalize_invoice_number(raw: str) -> str:
    """Ensure invoice number is in INV-XXXX format."""
    if not raw:
        return ""
    raw = raw.strip()
    # Already correct format
    if re.match(r'^INV-\d+$', raw, re.IGNORECASE):
        return raw.upper()
    # Just digits
    if re.match(r'^\d+$', raw):
        return f"INV-{raw}"
    # INV 1002 with space
    match = re.match(r'^INV\s+(\d+)$', raw, re.IGNORECASE)
    if match:
        return f"INV-{match.group(1)}"
    # Has a number somewhere
    digits = re.search(r'\d+', raw)
    if digits:
        return f"INV-{digits.group()}"
    return raw


def extract_vendor_name(vendor) -> str:
    """Handle vendor as string or nested dict {"name": ..., "address": ...}."""
    if isinstance(vendor, dict):
        return vendor.get("name", "")
    return str(vendor or "").strip()


# ---------------------------------------------------------------------------
# Format-specific pre-processors
# Convert each format into a clean text representation for the LLM
# ---------------------------------------------------------------------------

def process_json(raw_text: str) -> str:
    """Parse JSON invoice and return normalized text for LLM."""
    try:
        data = json.loads(raw_text)
        vendor = extract_vendor_name(data.get("vendor", ""))
        inv_num = normalize_invoice_number(str(data.get("invoice_number", "")))
        due_date = data.get("due_date", "")
        total = data.get("total", data.get("subtotal", 0))
        currency = data.get("currency", "USD")
        lines = [
            f"Invoice Number: {inv_num}",
            f"Vendor: {vendor}",
            f"Date: {data.get('date', '')}",
            f"Due Date: {due_date}",
            f"Currency: {currency}",
            f"Total: {total}",
            f"Payment Terms: {data.get('payment_terms', '')}",
            "",
            "Line Items:",
        ]
        for item in data.get("line_items", []):
            name = normalize_item_name(item.get("item", item.get("description", "")))
            qty = item.get("quantity", 0)
            price = item.get("unit_price", 0)
            note = item.get("note", "")
            line = f"  {name}  qty: {qty}  unit_price: {price}"
            if note:
                line += f"  ({note})"
            lines.append(line)
        return "\n".join(lines)
    except Exception:
        return raw_text


def process_xml(raw_text: str) -> str:
    """Parse XML invoice and return normalized text for LLM."""
    try:
        root = ET.fromstring(raw_text)

        def find(tag):
            el = root.find(f".//{tag}")
            return el.text.strip() if el is not None and el.text else ""

        inv_num = normalize_invoice_number(find("invoice_number"))
        vendor = find("vendor")
        lines = [
            f"Invoice Number: {inv_num}",
            f"Vendor: {vendor}",
            f"Date: {find('date')}",
            f"Due Date: {find('due_date')}",
            f"Currency: {find('currency') or 'USD'}",
            f"Payment Terms: {find('payment_terms')}",
            "",
            "Line Items:",
        ]
        for item in root.findall(".//item"):
            name_el = item.find("name")
            qty_el  = item.find("quantity")
            price_el = item.find("unit_price")
            name  = normalize_item_name(name_el.text.strip() if name_el is not None else "")
            qty   = qty_el.text.strip() if qty_el is not None else "0"
            price = price_el.text.strip() if price_el is not None else "0"
            lines.append(f"  {name}  qty: {qty}  unit_price: {price}")

        total_el = root.find(".//total")
        if total_el is not None:
            lines.append(f"\nTotal: {total_el.text.strip()}")

        return "\n".join(lines)
    except Exception:
        return raw_text


def process_csv(raw_text: str) -> str:
    """
    Handle two CSV formats:
    1. Vertical key-value: field,value (stacked key-value pairs, items accumulate as current_item state)
    2. Horizontal tabular: Invoice Number,Vendor,Date,...,Item,Qty,... (one row per line item)
    
    Format detection: first row = ["field", "value"] -> vertical; otherwise horizontal with headers.
    Horizontal uses flexible header matching (col() helper) to tolerate column order variations.
    """
    try:
        reader = csv.reader(io.StringIO(raw_text.strip()))
        rows = [r for r in reader if any(c.strip() for c in r)]
        if not rows:
            return raw_text

        # Detect format by first row
        first_row = [c.strip().lower() for c in rows[0]]

        # Vertical key-value format (field, value)
        # State machine: accumulate item attributes (qty, price) as we see them, flush to items[] when new item encountered
        if first_row[:2] == ["field", "value"]:
            kv = {}
            items = []
            current_item = {}
            for row in rows[1:]:
                if len(row) < 2:
                    continue
                key = row[0].strip().lower()
                val = row[1].strip()
                if key == "item":
                    # Item key triggers flush of previous item (if any) and starts new item
                    if current_item:
                        items.append(current_item)
                    current_item = {"name": normalize_item_name(val)}
                elif key == "quantity" and current_item:
                    current_item["qty"] = val
                elif key == "unit_price" and current_item:
                    current_item["price"] = val
                else:
                    kv[key] = val
            if current_item:
                items.append(current_item)

            inv_num = normalize_invoice_number(kv.get("invoice_number", ""))
            lines = [
                f"Invoice Number: {inv_num}",
                f"Vendor: {kv.get('vendor', '')}",
                f"Date: {kv.get('date', '')}",
                f"Due Date: {kv.get('due_date', '')}",
                f"Total: {kv.get('total', '')}",
                f"Payment Terms: {kv.get('payment_terms', '')}",
                "",
                "Line Items:",
            ]
            for it in items:
                lines.append(f"  {it.get('name','')}  qty: {it.get('qty','')}  unit_price: {it.get('price','')}")
            return "\n".join(lines)

        # Horizontal tabular format
        # Expected headers: Invoice Number, Vendor, Date, Due Date, Item, Qty, Unit Price, ...
        headers = [c.strip().lower() for c in rows[0]]
        data_rows = rows[1:]

        def col(row, *names):
            # Flexible header matching: searches for substring match in header names
            # Tolerates column reordering and handles headers like "Unit Price", "unit_price", "UnitPrice" uniformly
            for name in names:
                for i, h in enumerate(headers):
                    if name in h and i < len(row):
                        return row[i].strip()
            return ""

        inv_num = ""
        vendor = ""
        date = ""
        due_date = ""
        items = []
        total = ""

        for row in data_rows:
            if not any(c.strip() for c in row):
                continue
            inv_val = col(row, "invoice")
            if inv_val and re.search(r'\d', inv_val):
                inv_num = normalize_invoice_number(inv_val)
                vendor  = col(row, "vendor")
                date    = col(row, "date")
                due_date = col(row, "due date", "due_date")

            item_val = col(row, "item")
            qty_val  = col(row, "qty", "quantity")
            price_val = col(row, "unit price", "unit_price")

            if item_val and re.search(r'[a-zA-Z]', item_val):
                items.append({
                    "name":  normalize_item_name(item_val),
                    "qty":   qty_val,
                    "price": price_val,
                })

            total_val = col(row, "total")
            if total_val and re.search(r'\d', total_val):
                total = total_val

        lines = [
            f"Invoice Number: {inv_num}",
            f"Vendor: {vendor}",
            f"Date: {date}",
            f"Due Date: {due_date}",
            f"Total: {total}",
            "",
            "Line Items:",
        ]
        for it in items:
            lines.append(f"  {it['name']}  qty: {it['qty']}  unit_price: {it['price']}")
        return "\n".join(lines)

    except Exception:
        return raw_text


def process_pdf(invoice_path: str) -> str:
    """Extract text from PDF using pdfplumber."""
    try:
        import pdfplumber
        with pdfplumber.open(invoice_path) as pdf:
            text = "\n".join(
                page.extract_text() or "" for page in pdf.pages
            )
        return text.strip()
    except ImportError:
        return "[PDF] pdfplumber not installed - install with: pip install pdfplumber"
    except Exception as e:
        return f"[PDF] Failed to extract text: {e}"


def normalize_text(raw_text: str) -> str:
    """
    Light normalization of plain text invoices before LLM:
    - Fix OCR errors: letter O misread as zero in years (2O26 -> 2026)
    - Normalize item names with spaces to canonical form (Widget A -> WidgetA)
    Runs before LLM to reduce extraction variance from OCR/format noise.
    """
    # OCR correction: dates like '2O26' or '1O23' where O is mistaken for 0
    text = re.sub(r'\b(2[0O]2[0-9])\b', lambda m: m.group(0).replace('O', '0'), raw_text)
    # Normalize spaced item names to match validation inventory (Widget A -> WidgetA)
    for spaced, joined in [("Widget A", "WidgetA"), ("Widget B", "WidgetB"), ("Gadget X", "GadgetX")]:
        text = re.sub(re.escape(spaced), joined, text, flags=re.IGNORECASE)
    return text


# ---------------------------------------------------------------------------
# LLM prompts
# ---------------------------------------------------------------------------

EXTRACT_SYSTEM = """You are an expert invoice data extraction agent for Acme Corp's AP automation system.
Extract structured data from the invoice text provided. The text may already be pre-processed.

Respond ONLY with a JSON object. No markdown, no explanation, just raw JSON.

Schema:
{
  "invoice_number": "string in INV-XXXX format, or empty string",
  "vendor": "vendor name as a plain string",
  "amount": float (total amount as number, 0.0 if not found),
  "currency": "USD or other currency code, default USD",
  "due_date": "YYYY-MM-DD or empty string if invalid or not found",
  "line_items": [
    {
      "description": "normalized item name e.g. WidgetA",
      "quantity": float,
      "unit_price": float
    }
  ],
  "extraction_confidence": float 0.0-1.0,
  "extraction_notes": "note any issues, typos corrected, missing fields, suspicious data"
}

Rules:
- vendor.name in nested objects should be extracted as a plain string
- Normalize item names: 'Widget A' -> 'WidgetA', 'Gadget X' -> 'GadgetX'
- Invoice numbers: prefix bare numbers with INV- e.g. '1002' -> 'INV-1002'
- Quantities written as 'x10', '10 units', 'qty: 10' should all become 10
- Dates like 'yesterday', 'immediately' are invalid, use empty string
- Negative quantities are a data integrity issue, keep as-is and note it
- All currencies accepted, note non-USD in extraction_notes
- Set confidence below 0.7 for messy/incomplete invoices"""

CRITIQUE_SYSTEM = """You are a quality-control agent reviewing an invoice data extraction.
Given the original invoice text and an initial extraction, identify errors or missed fields
and produce a corrected improved extraction.

Respond ONLY with a JSON object in the same schema. No markdown."""


def safe_parse_json(text: str) -> dict:
    text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
    return json.loads(text)


# ---------------------------------------------------------------------------
# Main agent function
# ---------------------------------------------------------------------------

def run_ingestion(state: dict) -> dict:
    """
    Reads:   raw_text, invoice_path
    Returns: extracted, status, log, errors
    """
    raw_text     = state.get("raw_text", "")
    invoice_path = state.get("invoice_path", "")
    log    = []
    errors = []

    log.append("[Ingestion] Starting extraction...")

    # Step 1: Format-specific pre-processing
    ext = os.path.splitext(invoice_path)[1].lower()

    if ext == ".json":
        log.append("[Ingestion] Detected JSON format")
        processed_text = process_json(raw_text)
    elif ext == ".xml":
        log.append("[Ingestion] Detected XML format")
        processed_text = process_xml(raw_text)
    elif ext == ".csv":
        log.append("[Ingestion] Detected CSV format")
        processed_text = process_csv(raw_text)
    elif ext == ".pdf":
        log.append("[Ingestion] Detected PDF format - extracting text")
        processed_text = process_pdf(invoice_path)
        processed_text = normalize_text(processed_text)
    else:
        # TXT and anything else
        log.append("[Ingestion] Detected TXT format")
        processed_text = normalize_text(raw_text)

    log.append("[Ingestion] Pre-processing complete, sending to LLM...")

    # Step 2: LLM first pass extraction
    try:
        response1, model_used = call_llm(EXTRACT_SYSTEM, f"Extract invoice data from:\n\n{processed_text}")
        log.append(f"[Ingestion] Using LLM: {model_used}")
        extracted = safe_parse_json(response1)
        log.append(f"[Ingestion] Initial extraction confidence: {extracted.get('extraction_confidence', '?')}")
    except Exception as e:
        errors.append(f"Ingestion extraction failed: {e}")
        log.append(f"[Ingestion] ERROR: {e}")
        return {
            "extracted": None,
            "status": "error",
            "log": log,
            "errors": errors,
        }

    # Step 3: Confidence-based self-correction
    # Threshold 0.75: low confidence (messy invoice, many variants) triggers second LLM pass
    # Critique LLM catches: missed nested fields (vendor.name), item name inconsistencies, quantity formats
    confidence = extracted.get("extraction_confidence", 1.0)
    if confidence < 0.75:
        log.append(f"[Ingestion] Low confidence ({confidence}), running critique pass...")
        try:
            critique_prompt = (
                f"Original invoice text:\n{processed_text}\n\n"
                f"Initial extraction:\n{json.dumps(extracted, indent=2)}\n\n"
                f"Review and correct any errors. Pay attention to: "
                f"missing fields, wrong item names, incorrect quantities, bad dates, nested vendor fields."
            )
            response2, _ = call_llm(CRITIQUE_SYSTEM, critique_prompt)
            extracted = safe_parse_json(response2)
            log.append(f"[Ingestion] Post-critique confidence: {extracted.get('extraction_confidence', '?')}")
        except Exception as e:
            log.append(f"[Ingestion] Critique pass failed ({e}), using initial extraction")

    # Step 4: Post-LLM deterministic normalization
    # Override LLM outputs with canonical forms to handle format variations LLM might miss:
    # - Invoice numbers like 'INV 1002' or bare '1002' -> 'INV-1002'
    # - Nested vendor objects (vendor.name) -> flat string
    # - Item names with spaces or case variants -> standard form
    extracted["invoice_number"] = normalize_invoice_number(
        str(extracted.get("invoice_number", ""))
    )
    # Normalize vendor
    extracted["vendor"] = extract_vendor_name(extracted.get("vendor", ""))

    # Normalize item names in line items
    for item in extracted.get("line_items", []):
        item["description"] = normalize_item_name(item.get("description", ""))

    # Step 5: Note any missing fields
    issues = []
    if not extracted.get("vendor"):
        issues.append("Missing vendor name")
    if not extracted.get("invoice_number"):
        issues.append("Missing invoice number")
    if extracted.get("amount", 0) == 0:
        issues.append("Amount is zero")

    if issues:
        log.append(f"[Ingestion] Field issues noted: {', '.join(issues)}")
        extracted["extraction_notes"] = (
            extracted.get("extraction_notes", "") + " | " + "; ".join(issues)
        ).strip(" |")

    log.append(
        f"[Ingestion] Done. Invoice: {extracted.get('invoice_number')} "
        f"| Vendor: {extracted.get('vendor')} "
        f"| Amount: {extracted.get('currency','USD')} {extracted.get('amount', 0):.2f}"
    )

    return {
        "extracted": extracted,
        "status": "extracted",
        "log": log,
        "errors": errors,
    }
