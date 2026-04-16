"""
agents/approval_agent.py
VP-level approval agent with LLM reasoning + self-critique loop.
Invoices over $10K face elevated scrutiny.

LangGraph 1.1.6 pattern:
- Read only what you need from state
- Return ONLY the keys this agent changes
- LangGraph merges them back into full state automatically
"""
import json
import re
import os
from llm_client import call_llm

HIGH_VALUE_THRESHOLD = 10_000.0

APPROVAL_SYSTEM = """You are a VP-level financial approver at Acme Corp, a PE-backed manufacturing firm.
You are reviewing invoices for approval. You must reason carefully about risk, compliance, and business need.

You will receive:
- Invoice data (vendor, amount, items)
- Validation results (flags, warnings)

Your decision criteria:
- Invoices with critical flags (UNKNOWN_ITEM, OUT_OF_STOCK, INVALID_QUANTITY, UNTRUSTED_VENDOR) should be REJECTED
- Invoices over $10,000 require extra scrutiny - look for PO references, trusted vendors, reasonable pricing
- Invoices with only warnings (not hard flags) may still be approved with conditions noted
- Consider: vendor trust, price reasonableness, quantity sensibility, due date urgency

Respond ONLY with a JSON object:
{
  "decision": "approved" or "rejected",
  "reasoning": "2-4 sentences explaining the decision",
  "risk_score": float 0.0-10.0 (0=lowest risk, 10=highest),
  "conditions": "any conditions or follow-up actions required, or empty string",
  "requires_vp": true or false
}"""

CRITIQUE_SYSTEM = """You are a second-opinion financial compliance officer reviewing an approval decision.
Evaluate whether the decision is sound, identify any overlooked risks or overly cautious rejections,
and produce a final corrected decision.

Respond ONLY with the same JSON schema. No markdown."""



def safe_parse(text: str) -> dict:
    text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
    return json.loads(text)

def check_invoice_aging(due_date: str) -> dict:
    from datetime import datetime
    today = datetime.today().date()
    
    if not due_date or due_date.lower() in ["", "none", "null", "invalid"]:
        return {"status": "unknown", "days": None, "message": "No valid due date"}
    
    try:
        due = datetime.strptime(due_date, "%Y-%m-%d").date()
        days = (due - today).days
        
        if days < 0:
            return {"status": "overdue", "days": abs(days), "message": f"OVERDUE by {abs(days)} days"}
        elif days <= 3:
            return {"status": "critical", "days": days, "message": f"CRITICAL - due in {days} days"}
        elif days <= 7:
            return {"status": "soon", "days": days, "message": f"Due soon - {days} days remaining"}
        else:
            return {"status": "normal", "days": days, "message": f"Normal - {days} days remaining"}
    except Exception:
        return {"status": "unknown", "days": None, "message": "Could not parse due date"}

def build_approval_prompt(extracted: dict, validation: dict) -> str:
    flags = validation.get("flags", [])
    warnings = validation.get("warnings", [])
    item_checks = validation.get("item_checks", [])
    amount = extracted.get("amount", 0)

    lines = [
        f"Invoice Number: {extracted.get('invoice_number', 'N/A')}",
        f"Vendor: {extracted.get('vendor', 'Unknown')}",
        f"Total Amount: ${amount:,.2f}",
        f"Due Date: {extracted.get('due_date', 'N/A')}",
        f"Invoice Aging: {check_invoice_aging(extracted.get('due_date', '')).get('message')}",
        f"High-Value (>$10K): {'YES - requires extra scrutiny' if amount > HIGH_VALUE_THRESHOLD else 'No'}",
        "",
        "Line Items:",
    ]
    for item in item_checks:
        lines.append(
            f"  - {item['item']}: qty={item['quantity_requested']}, status={item['status']}"
    )
    lines += [
        "",
        f"Validation Flags ({len(flags)}): {'; '.join(flags) if flags else 'None'}",
        f"Validation Warnings ({len(warnings)}): {'; '.join(warnings) if warnings else 'None'}",
        "",
        "Make your approval decision.",
    ]
    return "\n".join(lines)


def run_approval(state: dict) -> dict:
    """
    Reads:   extracted, validation
    Returns: approval, status, log, errors
    """
    extracted = state.get("extracted") or {}
    validation = state.get("validation") or {}
    log = []
    errors = []

    log.append("[Approval] Starting approval review...")

    amount = extracted.get("amount", 0)
    requires_vp = amount > HIGH_VALUE_THRESHOLD
    flags = validation.get("flags", [])

    if requires_vp:
        log.append(f"[Approval] High-value invoice (${amount:,.2f}) - elevated VP scrutiny required")

    # Hard reject fast path for data integrity violations - no LLM needed
    hard_reject_keywords = ["INVALID_QUANTITY", "INVALID_PRICE"]
    hard_rejects = [f for f in flags if any(k in f for k in hard_reject_keywords)]
    if hard_rejects:
        log.append(f"[Approval] Hard reject - data integrity violation: {hard_rejects}")
        approval = {
            "decision": "rejected",
            "reasoning": (
                f"Automatic rejection due to data integrity violations: {'; '.join(hard_rejects)}. "
                f"These indicate corrupted or fraudulent invoice data."
            ),
            "risk_score": 10.0,
            "conditions": "",
            "requires_vp": requires_vp,
            "critique": "Hard rejection - bypassed LLM due to unambiguous data integrity errors.",
        }
        return {
            "approval": approval,
            "status": "rejected",
            "log": log,
            "errors": errors,
        }

    # LLM first pass
    prompt = build_approval_prompt(extracted, validation)
    try:
        resp1, model_used = call_llm(APPROVAL_SYSTEM, prompt)
        log.append(f"[Approval] Using LLM: {model_used}")
        decision = safe_parse(resp1)
        log.append(f"[Approval] Initial decision: {decision.get('decision')} (risk={decision.get('risk_score')})")
    except Exception as e:
        errors.append(f"Approval LLM failed: {e}")
        log.append(f"[Approval] LLM ERROR: {e}")
        return {
            "approval": None,
            "status": "error",
            "log": log,
            "errors": errors,
        }

    # Self-critique loop
    try:
        critique_prompt = (
            f"Original invoice context:\n{prompt}\n\n"
            f"Initial approval decision:\n{json.dumps(decision, indent=2)}\n\n"
            f"Review this decision for soundness. Are there overlooked risks? "
            f"Is a rejection overly cautious? Produce your final corrected decision."
        )
        resp2, model_used = call_llm(CRITIQUE_SYSTEM, critique_prompt)
        final = safe_parse(resp2)
        final["critique"] = f"Reviewed. Original: {decision.get('decision')} -> Final: {final.get('decision')}"
        if decision.get("decision") != final.get("decision"):
            log.append(f"[Approval] Critique changed decision: {decision.get('decision')} -> {final.get('decision')}")
        else:
            log.append(f"[Approval] Critique confirmed decision: {final.get('decision')}")
    except Exception as e:
        log.append(f"[Approval] Critique pass failed ({e}), using initial decision")
        final = decision
        final["critique"] = "Critique pass skipped due to error."

    final["requires_vp"] = requires_vp
    final_status = "approved" if final.get("decision") == "approved" else "rejected"
    log.append(
        f"[Approval] Final: {final_status} | Risk: {final.get('risk_score')} "
        f"| {final.get('reasoning', '')[:80]}..."
    )

    return {
        "approval": final,
        "status": final_status,
        "log": log,
        "errors": errors,
    }
