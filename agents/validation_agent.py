"""
agents/validation_agent.py

Validates extracted invoice data against the SQLite inventory database.

Logic:
- Groups line items by name and sums quantities (one order may have same item
  across multiple lines e.g. regular + volume discount + replacement)
- Checks summed quantity against available stock
- Flags: UNKNOWN_ITEM, OUT_OF_STOCK, STOCK_EXCEEDED, INVALID_QUANTITY

LangGraph 1.1.6 pattern:
- Read only what you need from state
- Return ONLY the keys this agent changes
- LangGraph merges them back into full state automatically
"""
import sqlite3
import os
from collections import defaultdict

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "inventory.db")


def get_db():
    return sqlite3.connect(DB_PATH)


def run_validation(state: dict) -> dict:
    """
    Reads:   extracted
    Returns: validation, status, log, errors
    """
    extracted = state.get("extracted") or {}
    log    = []
    errors = []

    log.append("[Validation] Starting validation...")

    if not extracted:
        errors.append("No extracted data to validate")
        return {
            "validation": None,
            "status": "error",
            "log": log,
            "errors": errors,
        }

    line_items = extracted.get("line_items", [])
    all_flags  = []
    item_checks = []

    if not line_items:
        log.append("[Validation] WARNING: No line items found in invoice")

    try:
        conn   = get_db()
        cursor = conn.cursor()

        # Step 1: Check for invalid quantities first (data integrity)
        invalid_items = []
        valid_items   = []
        for item in line_items:
            qty  = item.get("quantity", 0)
            name = item.get("description", "")
            if qty <= 0:
                flag = f"INVALID_QUANTITY: '{name}' has quantity={qty} which is non-positive"
                all_flags.append(flag)
                invalid_items.append({"item": name, "quantity_requested": qty, "status": "error", "flag": flag})
            else:
                valid_items.append(item)

        # Step 2: Sum quantities per item across all valid line items
        quantity_totals = defaultdict(float)
        for item in valid_items:
            name = item.get("description", "")
            qty  = item.get("quantity", 0)
            quantity_totals[name] += qty

        # Step 3: Check each unique item against inventory
        checked_items = set()
        for item in valid_items:
            name = item.get("description", "")
            if name in checked_items:
                continue
            checked_items.add(name)

            total_qty = quantity_totals[name]

            cursor.execute("SELECT stock FROM inventory WHERE item = ?", (name,))
            row = cursor.fetchone()

            if row is None:
                flag = f"UNKNOWN_ITEM: '{name}' not found in inventory"
                all_flags.append(flag)
                item_checks.append({
                    "item": name,
                    "quantity_requested": total_qty,
                    "status": "error",
                    "flag": flag,
                })
                continue

            stock = row[0]

            if stock == 0:
                flag = f"OUT_OF_STOCK: '{name}' has 0 units in stock"
                all_flags.append(flag)
                item_checks.append({
                    "item": name,
                    "quantity_requested": total_qty,
                    "stock_available": 0,
                    "status": "error",
                    "flag": flag,
                })
                continue

            if total_qty > stock:
                flag = (
                    f"STOCK_EXCEEDED: '{name}' total requested {total_qty} "
                    f"exceeds available stock of {stock}"
                )
                all_flags.append(flag)
                item_checks.append({
                    "item": name,
                    "quantity_requested": total_qty,
                    "stock_available": stock,
                    "status": "error",
                    "flag": flag,
                })
                continue

            item_checks.append({
                "item": name,
                "quantity_requested": total_qty,
                "stock_available": stock,
                "status": "ok",
                "flag": None,
            })
            log.append(f"[Validation] '{name}': requested {total_qty}, stock {stock} - OK")

        # Add invalid items to checks
        item_checks.extend(invalid_items)

        conn.close()

    except Exception as e:
        errors.append(f"Validation DB error: {e}")
        log.append(f"[Validation] DB ERROR: {e}")
        return {
            "validation": None,
            "status": "error",
            "log": log,
            "errors": errors,
        }

    passed = len(all_flags) == 0
    status = "validated" if passed else "flagged"

    validation = {
        "passed": passed,
        "flags":  all_flags,
        "item_checks": item_checks,
    }

    if all_flags:
        log.append(f"[Validation] FAILED - {len(all_flags)} flag(s):")
        for f in all_flags:
            log.append(f"[Validation]   {f}")
    else:
        log.append(f"[Validation] PASSED - all {len(item_checks)} unique item(s) validated OK")

    return {
        "validation": validation,
        "status":     status,
        "log":        log,
        "errors":     errors,
    }
