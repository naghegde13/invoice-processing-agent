"""
main.py - CLI entry point for the Galatiq invoice processing system.

Usage:
    python main.py --invoice_path=data/invoices/INV-1001.txt
    python main.py --invoice_path=data/invoices/INV-1002.txt
    python main.py --run_all
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pipeline import process_invoice

INVOICES_DIR = os.path.join(os.path.dirname(__file__), "data", "invoices")

STATUS_EMOJI = {
    "paid":      "✅",
    "approved":  "✅",
    "rejected":  "❌",
    "flagged":   "⚠️",
    "error":     "💥",
    "pending":   "⏳",
    "extracted": "📄",
    "validated": "🔍",
}


def print_result(state: dict):
    status = state.get("status", "unknown")
    emoji = STATUS_EMOJI.get(status, "❓")
    extracted = state.get("extracted") or {}
    validation = state.get("validation") or {}
    approval = state.get("approval") or {}
    payment = state.get("payment") or {}

    print("\n" + "="*60)
    print(f"  {emoji}  INVOICE RESULT: {status.upper()}")
    print("="*60)
    print(f"  Invoice:  {extracted.get('invoice_number', 'N/A')}")
    print(f"  Vendor:   {extracted.get('vendor', 'N/A')}")
    print(f"  Amount:   ${extracted.get('amount', 0):,.2f}")
    print(f"  Due Date: {extracted.get('due_date', 'N/A')}")
    
    if extracted.get("due_date"):
        from agents.approval_agent import check_invoice_aging
        aging = check_invoice_aging(extracted.get("due_date", ""))
        aging_emoji = "🔴" if aging["status"] == "overdue" else "🟡" if aging["status"] in ["critical", "soon"] else "🟢"
        print(f"  Aging: {aging['message']} {aging_emoji}")
    
    print(f"  Confidence: {extracted.get('extraction_confidence', 'N/A')}")
    fraud = state.get("fraud") or {}
    if fraud:
        rec = fraud.get("recommendation", "")
        fscore = fraud.get("score", "N/A")
        color = "🔴" if rec == "high_risk" else "🟡" if rec == "suspicious" else "🟢"
        print(f"  Fraud Score: {fscore}/10 {color} ({rec})")
        for sig in fraud.get("signals", []):
            print(f"    ⚠️  {sig}")

    if validation:
        flags = validation.get("flags", [])
        warnings = validation.get("warnings", [])
        print(f"\n  Validation: {'FAILED' if flags else 'PASSED'}")
        for f in flags:
            print(f"    🚩 {f}")
        for w in warnings:
            print(f"    ⚠️  {w}")

    if approval:
        print(f"\n  Approval:  {approval.get('decision', 'N/A').upper()}")
        print(f"  Risk Score: {approval.get('risk_score', 'N/A')}/10")
        print(f"  Reasoning: {approval.get('reasoning', '')[:120]}")
        if approval.get("conditions"):
            print(f"  Conditions: {approval.get('conditions')}")

    if payment:
        if payment.get("transaction_id"):
            print(f"\n  Payment TXN: {payment.get('transaction_id')}")
        elif payment.get("status") == "rejected":
            print(f"\n  Payment: NOT ISSUED (rejected)")

    if state.get("errors"):
        print(f"\n  Errors:")
        for e in state["errors"]:
            print(f"    💥 {e}")

    print("\n  Processing Log:")
    for entry in state.get("log", []):
        print(f"    {entry}")
    print("="*60 + "\n")

def write_summary_report(states: list, results: dict):
    from datetime import datetime
    lines = [
        "INVOICE PROCESSING BATCH SUMMARY",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "="*60,
        f"Total Processed: {len(states)}",
        f"Paid:            {results['paid']}",
        f"Rejected:        {results['rejected']}",
        f"Duplicates:      {results.get('duplicate', 0)}",
        f"Errors:          {results['error']}",
        "="*60,
        "",
        "INVOICE DETAILS:",
        "",
    ]
    for state in states:
        ext = state.get("extracted") or {}
        status = state.get("status", "unknown").upper()
        inv = ext.get("invoice_number", "N/A")
        vendor = ext.get("vendor", "N/A")
        amount = ext.get("amount", 0)
        confidence = ext.get("extraction_confidence", "N/A")
        flags = (state.get("validation") or {}).get("flags", [])
        fraud_signals = (state.get("fraud") or {}).get("signals", [])
        fraud_score = (state.get("fraud") or {}).get("score", None)
        txn = (state.get("payment") or {}).get("transaction_id", "")
        lines.append(f"  {inv} | {vendor} | ${amount:,.2f} | {status} | confidence: {confidence}")
        if fraud_score is not None:
            lines.append(f"    FRAUD SCORE: {fraud_score}/10 ({(state.get('fraud') or {}).get('recommendation', '')})")
            for sig in fraud_signals:
                lines.append(f"    FRAUD SIGNAL: {sig}")
            if flags:
                for f in flags:
                    lines.append(f"    FLAG: {f}")
            if txn:
                lines.append(f"    TXN: {txn}")
        lines.append("")

    report_path = "summary_report.txt"
    with open(report_path, "w") as f:
        f.write("\n".join(lines))
    print(f"\n  📄 Summary report saved to: {report_path}")

def run_single(invoice_path: str):
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Processing: {invoice_path}")
    try:
        state = process_invoice(invoice_path)
        print_result(state)
        return state
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"\n💥 Fatal error processing {invoice_path}: {e}")
        return None


def run_all():
    if not os.path.exists(INVOICES_DIR):
        print(f"No invoices directory found at {INVOICES_DIR}")
        sys.exit(1)

    files = sorted([
        os.path.join(INVOICES_DIR, f)
        for f in os.listdir(INVOICES_DIR)
        if f.endswith((".txt", ".csv", ".json"))
    ])

    if not files:
        print("No invoice files found.")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  BATCH RUN: {len(files)} invoices")
    print(f"{'='*60}")

    results = {"paid": 0, "rejected": 0, "error": 0, "duplicate": 0}
    all_states = []
    for path in files:
        state = run_single(path)
        if state:
            all_states.append(state)
            s = state.get("status", "error")
            if s == "paid":
                results["paid"] += 1
            elif s == "rejected":
                results["rejected"] += 1
            elif s == "duplicate":
                results["duplicate"] += 1
            else:
                results["error"] += 1

    print(f"\n{'='*60}")
    print(f"  BATCH COMPLETE")
    print(f"  ✅ Paid:     {results['paid']}")
    print(f"  ❌ Rejected: {results['rejected']}")
    print(f"  💥 Errors:   {results['error']}")
    print(f"{'='*60}\n")
    write_summary_report(all_states, results)


def main():
    parser = argparse.ArgumentParser(description="Galatiq Invoice Processing System")
    parser.add_argument("--invoice_path", type=str, help="Path to a single invoice file")
    parser.add_argument("--run_all", action="store_true", help="Process all invoices in data/invoices/")
    args = parser.parse_args()

    if not args.invoice_path and not args.run_all:
        parser.print_help()
        sys.exit(1)

    if args.run_all:
        run_all()
    else:
        run_single(args.invoice_path)


if __name__ == "__main__":
    main()
