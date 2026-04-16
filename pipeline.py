"""
pipeline.py - LangGraph orchestration for the invoice processing pipeline.
Wires Ingestion -> Validation -> Approval -> Payment with conditional routing.

LangGraph 1.1.6: Each node returns only its changed keys.
The framework merges them into the full InvoiceState automatically.
"""
import os
from langgraph.graph import StateGraph, END
from models import InvoiceState
from agents.ingestion_agent import run_ingestion
from agents.validation_agent import run_validation
from agents.approval_agent import run_approval
from agents.payment_agent import run_payment
from agents.ingestion_agent import normalize_invoice_number
from agents.fraud_agent import run_fraud_check

import json
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "inventory.db")


def load_invoice_text(path: str) -> str:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Invoice file not found: {path}")
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def route_after_validation(state: InvoiceState) -> str:
    """Always go to approval - approval agent handles flagged invoices too."""
    if state.get("status") == "error":
        return "payment"
    return "approval"


def route_after_approval(state: InvoiceState) -> str:
    """Always go to payment - payment agent handles both approved and rejected."""
    return "payment"


def build_pipeline():
    graph = StateGraph(InvoiceState)

    graph.add_node("ingestion",  run_ingestion)
    graph.add_node("validation", run_validation)
    graph.add_node("approval",   run_approval)
    graph.add_node("payment",    run_payment)
    graph.add_node("fraud_check", run_fraud_check)

    graph.set_entry_point("ingestion")
    graph.add_edge("ingestion", "fraud_check")
    graph.add_conditional_edges(
        "fraud_check",
        lambda state: "payment" if state.get("status") == "rejected" else "validation",
        {"validation": "validation", "payment": "payment"},
    )
    graph.add_conditional_edges(
        "validation",
        route_after_validation,
        {"approval": "approval", "payment": "payment"},
    )
    graph.add_conditional_edges(
        "approval",
        route_after_approval,
        {"payment": "payment"},
    )
    graph.add_edge("payment", END)

    return graph.compile()

def is_duplicate(invoice_number: str) -> bool:
    if not invoice_number:
        return False
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM processing_log WHERE invoice_number = ?", (invoice_number,))
    row = cursor.fetchone()
    conn.close()
    return row is not None

def process_invoice(invoice_path: str) -> dict:
    initial_state: InvoiceState = {
        "invoice_path": invoice_path,
        "raw_text":     load_invoice_text(invoice_path),
        "extracted":    None,
        "validation":   None,
        "approval":     None,
        "payment":      None,
        "fraud":        None,
        "status":       "pending",
        "errors":       [],
        "log":          [f"[Pipeline] Processing: {invoice_path}"],
    }

    # Duplicate detection before running pipeline
    
    raw_inv_num = normalize_invoice_number(
        os.path.splitext(os.path.basename(invoice_path))[0].replace("invoice_", "").upper()
    )
    is_revision = False
    try:
        data = json.loads(initial_state["raw_text"])
        if data.get("revision"):
            is_revision = True
    except Exception:
        pass

    if not is_revision and is_duplicate(raw_inv_num):
        return {
            "invoice_path": invoice_path,
            "raw_text": "",
            "extracted": {"invoice_number": raw_inv_num, "vendor": "", "amount": 0, "due_date": ""},
            "validation": None,
            "approval": None,
            "payment": None,
            "status": "duplicate",
            "errors": [f"Duplicate invoice: {raw_inv_num} already processed"],
            "log": [f"[Pipeline] DUPLICATE DETECTED: {raw_inv_num} already in processing log"],
        }

    app = build_pipeline()
    return app.invoke(initial_state)
