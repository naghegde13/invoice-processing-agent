"""
agents/payment_agent.py
Handles payment execution (mock) for approved invoices
and structured rejection logging for rejected ones.

LangGraph 1.1.6 pattern:
- Read only what you need from state
- Return ONLY the keys this agent changes
- LangGraph merges them back into full state automatically
"""
import uuid
import sqlite3
import json
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "inventory.db")


def mock_payment(vendor: str, amount: float) -> dict:
    transaction_id = f"TXN-{uuid.uuid4().hex[:8].upper()}"
    print(f"[PaymentAPI] Paid ${amount:,.2f} to {vendor} | TXN: {transaction_id}")
    return {
        "status": "success",
        "transaction_id": transaction_id,
        "vendor": vendor,
        "amount": amount,
        "timestamp": datetime.utcnow().isoformat(),
    }


def log_to_db(extracted: dict, approval: dict, validation: dict, status: str):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO processing_log
                (invoice_number, vendor, amount, status, flags, reasoning, processed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            extracted.get("invoice_number", ""),
            extracted.get("vendor", ""),
            extracted.get("amount", 0),
            status,
            json.dumps(validation.get("flags", []) + validation.get("warnings", [])),
            approval.get("reasoning", ""),
            datetime.utcnow().isoformat(),
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DB] Failed to log result: {e}")


def run_payment(state: dict) -> dict:
    """
    Reads:   extracted, approval, validation, status
    Returns: payment, status, log, errors
    """
    extracted  = state.get("extracted")  or {}
    approval   = state.get("approval")   or {}
    validation = state.get("validation") or {}
    current_status = state.get("status", "")
    log = []
    errors = []

    if current_status == "approved":
        log.append("[Payment] Invoice approved - initiating payment...")
        vendor = extracted.get("vendor", "Unknown")
        amount = extracted.get("amount", 0.0)

        result = mock_payment(vendor, amount)
        log.append(
            f"[Payment] SUCCESS | TXN: {result['transaction_id']} | "
            f"${amount:,.2f} -> {vendor}"
        )
        log_to_db(extracted, approval, validation, "paid")

        return {
            "payment": result,
            "status": "paid",
            "log": log,
            "errors": errors,
        }

    else:
        reason = approval.get("reasoning", "No reasoning provided")
        flags  = validation.get("flags", [])

        log.append("[Payment] Invoice REJECTED - no payment issued")
        log.append(f"[Payment] Reason: {reason}")
        if flags:
            log.append(f"[Payment] Flags: {'; '.join(flags)}")

        log_to_db(extracted, approval, validation, "rejected")

        return {
            "payment": {
                "status": "rejected",
                "reason": reason,
                "flags": flags,
            },
            "status": "rejected",
            "log": log,
            "errors": errors,
        }
