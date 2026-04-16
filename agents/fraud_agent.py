"""
agents/fraud_agent.py
Scores invoices for fraud risk based on signals in the raw text and extracted data.
Sits between ingestion and validation in the pipeline.
Fast-rejects if fraud score >= 8.

LangGraph 1.1.6 pattern:
- Read only what you need from state
- Return ONLY the keys this agent changes
"""
import re


# Language patterns indicating pressure tactics (common in invoice fraud/phishing)
URGENCY_PATTERNS = [
    r"pay immediately",
    r"urgent",
    r"wire transfer",
    r"avoid penalt",
    r"asap",
    r"overdue",
    r"final notice",
    r"immediate payment",
]

SUSPICIOUS_VENDOR_WORDS = [
    "fraud", "fake", "scam", "anonymous", "unknown",
    "noprod", "suspicious",
]


def score_fraud(raw_text: str, extracted: dict) -> dict:
    """
    Weighted scoring model: detects invoice fraud signals.
    Score aggregation: individual signals contribute fixed weights, capped at 10.0.
    Rationale: urgency + missing data + round amounts are common in social engineering attacks.
    """
    signals = []
    score = 0.0

    text_lower = raw_text.lower()

    # Check urgency language in raw text
    for pattern in URGENCY_PATTERNS:
        if re.search(pattern, text_lower):
            signals.append(f"URGENCY_LANGUAGE: '{pattern}' detected in invoice text")
            score += 1.5

    # Check suspicious vendor name
    vendor = (extracted.get("vendor") or "").lower()
    for word in SUSPICIOUS_VENDOR_WORDS:
        if word in vendor:
            signals.append(f"SUSPICIOUS_VENDOR: vendor name contains '{word}'")
            score += 2.5  # Vendor issues weighted higher (direct trust signal)

    # Check for email-style invoice (billing@ in raw text)
    if re.search(r'[\w.]+@[\w.]+', raw_text):
        signals.append("EMAIL_INVOICE: invoice appears to originate from an email")
        score += 1.0

    # Check due date anomalies
    due_date = (extracted.get("due_date") or "").lower()
    if not due_date or due_date in ["", "none", "null"]:
        signals.append("MISSING_DUE_DATE: no valid due date found")
        score += 1.0

    # Check missing vendor
    if not extracted.get("vendor"):
        signals.append("MISSING_VENDOR: no vendor name found")
        score += 1.5

    # Heuristic: large round amounts often indicate generated/placeholder invoices
    # Real invoices typically have taxed amounts or specific line-item totals (e.g., $10,347.82)
    amount = extracted.get("amount", 0)
    if amount >= 10000 and amount % 1000 == 0:
        signals.append(f"ROUND_AMOUNT: amount ${amount:,.0f} is a suspiciously round number")
        score += 1.0

    # Check missing invoice number
    if not extracted.get("invoice_number"):
        signals.append("MISSING_INVOICE_NUMBER: no invoice number found")
        score += 1.0

    score = min(round(score, 1), 10.0)

    # Thresholds: 8+ = immediate rejection (fraud confidence), 4-7 = flag for manual review, <4 = proceed
    if score >= 8:
        recommendation = "high_risk"
    elif score >= 4:
        recommendation = "suspicious"
    else:
        recommendation = "clear"

    return {
        "score": score,
        "signals": signals,
        "recommendation": recommendation,
    }


def run_fraud_check(state: dict) -> dict:
    """
    Reads:   raw_text, extracted
    Returns: fraud, status, log, errors
    """
    raw_text  = state.get("raw_text", "")
    extracted = state.get("extracted") or {}
    log    = []
    errors = []

    log.append("[Fraud] Starting fraud analysis...")

    result = score_fraud(raw_text, extracted)

    score          = result["score"]
    signals        = result["signals"]
    recommendation = result["recommendation"]

    if signals:
        for s in signals:
            log.append(f"[Fraud] Signal: {s}")

    log.append(f"[Fraud] Score: {score}/10 | Recommendation: {recommendation}")

    # Fast-exit if high fraud risk: prevents downstream abuse of validation/approval workflow
    # If invoice is likely malicious, no point validating line items or involving approval process
    if recommendation == "high_risk":
        log.append(f"[Fraud] HIGH RISK - fast rejecting without further processing")
        return {
            "fraud":  result,
            "status": "rejected",
            "log":    log,
            "errors": errors,
        }

    return {
        "fraud":  result,
        "status": state.get("status", "extracted"),
        "log":    log,
        "errors": errors,
    }