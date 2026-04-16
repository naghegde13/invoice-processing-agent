# Architectural Decision Record

This document explains the key technical decisions made in building the Galatiq Invoice Processing System and the reasoning behind each one.

---

## 1. Why LangGraph for orchestration?

LangGraph was chosen over alternatives like CrewAI and AutoGen because it gives explicit, fine-grained control over the agent graph. With LangGraph you define exactly which agent runs when, how state flows between them, and what conditional routing logic applies at each step. This matters in a financial processing context where the order of operations is critical and side effects (like triggering a payment) must only happen at the right point in the pipeline.

CrewAI and AutoGen are better suited for more autonomous, conversational multi-agent setups where agents negotiate with each other. For a deterministic pipeline like invoice processing — ingestion always before fraud check, fraud check always before validation — LangGraph's explicit graph model is a better fit. It also makes the system easier to audit, debug, and extend.

---

## 2. Why does the fraud agent sit between ingestion and validation?

Fraud detection needs to happen as early as possible, immediately after we have extracted the invoice data but before we commit any compute to deeper processing.

The reasoning is simple: if an invoice is obviously fraudulent — urgency language, suspicious vendor name, wire transfer requests, round number amounts — there is no business value in running it through inventory validation or LLM-based approval. Those are expensive operations, both in latency and API cost. The fraud agent acts as a pre-filter that fast-rejects high-risk invoices before they consume any further resources.

It is also conceptually distinct from validation. Validation checks whether the order is fulfillable against inventory. Fraud detection checks whether the invoice itself is trustworthy. These are separate concerns and separating them into dedicated agents keeps each one focused and testable.

---

## 3. Why is the validation agent pure Python with no LLM?

Validation against inventory is a deterministic, rule-based operation. An item either exists in the database or it does not. A requested quantity either exceeds stock or it does not. There is no ambiguity that requires language model reasoning.

Sending validation logic to an LLM introduces three problems. First, LLMs can hallucinate — they might incorrectly determine that an item is in stock when it is not, or vice versa. Second, LLMs are non-deterministic, meaning the same input can produce different outputs on different runs. Third, it adds latency and cost for something that a database query handles in milliseconds.

The principle here is to use LLMs only where their reasoning capability adds genuine value — extracting structure from messy text, making judgment calls under ambiguity. For deterministic checks against known data, Python code is faster, cheaper, and more reliable.

---

## 4. Why sum quantities across line items before checking stock?

An invoice may list the same item across multiple lines for legitimate business reasons — a regular order line, a volume discount line, an expedited line, a replacement unit. These are all real physical units being requested against the same inventory pool.

Checking each line individually would allow an order for 22 WidgetAs to pass validation if no single line exceeds the stock of 15, even though the combined order cannot be fulfilled. That would be a false pass and a real operational problem downstream.

Summing quantities per item first and then checking against stock is the correct business logic. It reflects how the warehouse would actually process the order — as a total quantity required, not as isolated line items.

---

## 5. Why does the critique loop exist in the approval agent?

The approval agent makes a consequential decision — whether to release payment. A single LLM pass is prone to missing context, being overly conservative, or being insufficiently cautious depending on how the prompt lands.

The critique loop adds a second independent pass that reviews the initial decision and asks: was this sound? Were there overlooked risks? Was the rejection overly cautious? This mirrors how financial decisions are made in practice — a checker reviews the approver's work before money moves.

In testing this proved its value. On one invoice, the first pass approved a significantly overdue invoice with minimal scrutiny. The critique pass caught the 73-day overdue status and changed the decision to rejected with conditions. That is exactly the kind of error a single-pass system would let through.

---

## 6. Why OpenAI as the fallback LLM?

The README specified xAI Grok as the primary LLM. Rather than just substituting OpenAI, the system was built with a unified `llm_client.py` that tries Grok first and falls back to OpenAI automatically if Grok is unavailable or the API key is not set.

OpenAI GPT-4o was chosen as the fallback because it is a strong general-purpose model with reliable JSON output and broad knowledge of business documents and financial language. Both Grok and OpenAI use the same chat completions API interface, making the switch seamless with no prompt changes required.

A caching layer was considered as an alternative fallback strategy but was deliberately not implemented here. Caching makes sense for repeated identical queries but invoice processing involves unique documents every time, making cache hit rates low and the added complexity not worth it for this use case.

The LLM used is logged per agent per invoice so the output is fully auditable and you can see exactly which model processed each decision.

---

## 7. Why is the ingestion agent the most complex agent?

The ingestion agent handles the most variability in the system. Invoice data arrives in five formats — TXT, JSON, CSV, XML, PDF — and within those formats there is significant variation. Two completely different CSV structures. JSON files with nested vendor objects. Text files with OCR-style typos and inconsistent layouts. PDFs with extracted text that needs cleaning.

All of this variability needs to be resolved before any downstream agent can work reliably. The ingestion agent handles format-specific pre-processing first, then passes normalized text to the LLM for structured extraction, then runs a post-LLM normalization pass to catch anything the LLM missed. The self-correction loop adds a second critique pass when extraction confidence is below 0.75.

The design principle is: resolve all ambiguity at ingestion so that validation, fraud detection, and approval receive clean, consistent data. Garbage in, garbage out — the ingestion agent is the quality gate for the entire pipeline.
