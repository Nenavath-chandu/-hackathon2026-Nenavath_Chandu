# System Architecture — Autonomous Support Resolution Agent

---

## 1. High-Level Component Map

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         main.py  (Entry Point)                           │
│                                                                          │
│  parse_args() → load_tickets() → asyncio.gather(N tasks)                │
│                                          │                               │
│                              asyncio.Semaphore(MAX_CONCURRENT=10)        │
│                              (controls max parallel tickets in flight)   │
└──────────────────────────────────────────┬───────────────────────────────┘
                                           │  one asyncio.Task per ticket
                                           ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                      agent.py  ·  SupportAgent                           │
│                                                                          │
│   ┌──────────────────────────────────────────────────────────────────┐   │
│   │                   ReAct Reasoning Loop                           │   │
│   │                                                                  │   │
│   │   OBSERVE ──▶ CLASSIFY ──▶ PLAN ──▶ ┌──────────────────────┐   │   │
│   │                                      │  THINK  (reasoning)  │   │   │
│   │                                      │       ↓              │   │   │
│   │                                      │  ACT   (tool call)   │───────────▶ tools.py
│   │                                      │       ↓              │   │   │
│   │                                      │  OBSERVE (result)    │   │   │
│   │                                      │       ↓              │   │   │
│   │                                      │  REFLECT (rescore)   │   │   │
│   │                                      └────────┬─────────────┘   │   │
│   │                                               │                  │   │
│   │                               ┌──────────────┴───────────────┐  │   │
│   │                               │  continue / resolve / escalate│  │   │
│   │                               └──────────────────────────────┘  │   │
│   └──────────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────┘
         │                   │                   │
         ▼                   ▼                   ▼
   planner.py          executor.py           logger.py
   Classifier          Executor              AuditLogger
   Planner             (retry loop)          (async JSON sink)
         │                   │
         ▼                   ▼
    config.py           tools.py
    (thresholds)        (8 tool fns)
```

---

## 2. Agent Reasoning Loop — Step by Step

```
  Ticket arrives (dict from tickets.json)
        │
        ▼
  ┌─────────────────────────────────────────────────────────┐
  │ STEP 1 · OBSERVE                                        │
  │                                                         │
  │  • Extract: ticket_id, email, order_id, subject, body   │
  │  • Sanitize: redact sensitive fields (password, CVV…)   │
  │  • Record step in AuditEntry                            │
  └──────────────────────────┬──────────────────────────────┘
                             │
                             ▼
  ┌─────────────────────────────────────────────────────────┐
  │ STEP 2 · CLASSIFY  (planner.Classifier)                 │
  │                                                         │
  │  Scoring over ticket text (subject + description):      │
  │                                                         │
  │  Intent detection (keyword scoring, 7 categories):      │
  │    refund_request / order_status / product_inquiry /    │
  │    technical_support / account_issue / complaint /      │
  │    unknown                                              │
  │                                                         │
  │  Urgency scoring (0.0–1.0):                             │
  │    "asap" +0.40, "fraud" +0.45, "broken" +0.20, …      │
  │                                                         │
  │  Priority mapping:                                      │
  │    urgency ≥ 0.70 → critical                            │
  │    urgency ≥ 0.40 → high                                │
  │    urgency ≥ 0.20 → medium                              │
  │    else           → low                                 │
  │                                                         │
  │  Confidence formula:                                    │
  │    conf = (best_matches / total_matches) × 0.5 + 0.40   │
  │    if resolvable intent: conf += 0.10                   │
  └──────────────────────────┬──────────────────────────────┘
                             │
                             ▼
  ┌─────────────────────────────────────────────────────────┐
  │ STEP 3 · PLAN  (planner.Planner)                        │
  │                                                         │
  │  Look up intent → tool pipeline in config.py:           │
  │                                                         │
  │  refund_request →                                       │
  │    [get_order, get_customer, check_refund_eligibility,  │
  │     issue_refund, send_reply]                           │
  │                                                         │
  │  order_status →                                         │
  │    [get_order, get_customer, search_knowledge_base,     │
  │     send_reply]                                         │
  │                                                         │
  │  complaint →                                            │
  │    [get_customer, get_order, search_knowledge_base,     │
  │     escalate]                                           │
  │                                                         │
  │  Pad plan to MIN_TOOL_CALLS (≥ 3) if needed             │
  │  Resolve ticket field values into tool arguments        │
  └──────────────────────────┬──────────────────────────────┘
                             │
                             ▼
  ┌─────────────────────────────────────────────────────────┐
  │ STEP 4 · ReAct LOOP  (repeated per plan step)           │
  │                                                         │
  │  THINK: Record why this tool is being called            │
  │                                                         │
  │  ACT:   executor.run(tool_name, args, audit_entry)      │
  │           └─ See Executor section below                 │
  │                                                         │
  │  OBSERVE: Store result in observations dict             │
  │           If tool failed: confidence −= 0.20            │
  │                                                         │
  │  REFLECT: Update confidence. Check:                     │
  │           • check_refund_eligibility result?            │
  │             → auto-update issue_refund amount           │
  │           • confidence < 0.50? → escalate early         │
  │           • all steps done?    → decide resolve/escalate│
  └──────────────────────────┬──────────────────────────────┘
                             │
             ┌───────────────┴────────────────┐
             │                                │
             ▼                                ▼
  ┌──────────────────────┐        ┌────────────────────────┐
  │  RESOLVE             │        │  ESCALATE              │
  │  · Compose reply     │        │  · Build summary msg   │
  │  · send_reply()      │        │  · escalate() tool     │
  │  · finalize audit    │        │  · finalize audit      │
  │  · status=resolved   │        │  · status=escalated    │
  └──────────────────────┘        └────────────────────────┘
```

---

## 3. Executor — Retry & Backoff Flow

```
  executor.run(tool_name, args)
        │
        ├─── Attempt 1 ──────────────────────────────────────────────┐
        │         │                                                   │
        │         ├── SUCCESS → record step → return result ✓         │
        │         │                                                   │
        │         └── FAILURE → log warning → wait 0.5s ─────────────┤
        │                                                             │
        ├─── Attempt 2 ──────────────────────────────────────────────┤
        │         │                                                   │
        │         ├── SUCCESS → record step → return result ✓         │
        │         │                                                   │
        │         └── FAILURE → log warning → wait 1.0s ─────────────┤
        │                                                             │
        └─── Attempt 3 (final) ──────────────────────────────────────┘
                  │
                  ├── SUCCESS → record step → return result ✓
                  │
                  └── FAILURE → raise ToolExecutionError
                                      │
                                      ▼
                              agent catches error
                                      │
                                      ├── confidence −= 0.20
                                      │
                                      └── if conf < 0.50 → ESCALATE
```

**Backoff formula:** `delay = base_delay × (backoff_factor ^ attempt_number)`
- Attempt 1 failure → wait `0.5 × 2⁰ = 0.5s`
- Attempt 2 failure → wait `0.5 × 2¹ = 1.0s`

---

## 4. Concurrency Model

```
  main.py — asyncio event loop
  │
  ├── asyncio.Semaphore(10)      ← maximum 10 tickets in-flight at once
  │
  ├── Task: TKT-2001 ──▶ SupportAgent.process()  ─── awaiting tools (non-blocking)
  ├── Task: TKT-2002 ──▶ SupportAgent.process()  ─── awaiting tools (non-blocking)
  ├── Task: TKT-2003 ──▶ SupportAgent.process()  ─── awaiting tools (non-blocking)
  ├── Task: TKT-2004 ──▶ SupportAgent.process()  ─── awaiting tools (non-blocking)
  ├── ...up to 10 concurrent
  │
  └── asyncio.gather(*tasks) ← waits for ALL tasks to finish, collects results

  Key: each tool call uses "await asyncio.sleep()" for I/O simulation.
  While one ticket waits for a tool, another ticket runs.
  True concurrency with zero OS threads.
```

---

## 5. Decision Flow — Resolve vs Escalate

```
  End of tool plan (or early trigger):
         │
         ▼
  ┌───────────────────────────────────────────┐
  │  confidence < 0.50?                       │──── YES ──▶ ESCALATE
  │  (Low confidence threshold)               │           reason: "Low confidence"
  └────────────────────────┬──────────────────┘
                           │ NO
                           ▼
  ┌───────────────────────────────────────────┐
  │  intent in ESCALATE_INTENTS?              │──── YES ──▶ ESCALATE
  │  (complaint / unknown)                    │           reason: "Intent policy"
  └────────────────────────┬──────────────────┘
                           │ NO
                           ▼
  ┌───────────────────────────────────────────┐
  │  reasoning steps > MAX_REASONING_STEPS?   │──── YES ──▶ ESCALATE
  │  (safety cap = 10)                        │           reason: "Max steps exceeded"
  └────────────────────────┬──────────────────┘
                           │ NO
                           ▼
                        RESOLVE
                  (send contextual reply)
```

---

## 6. Tool Data Flow — Refund Request Example

```
  Ticket: "I want a refund for my headphones" (TKT-2001)
  Intent: refund_request · Confidence: 0.83 · Priority: medium

  ┌──────────────┐     order_id="ORD-1001"      ┌─────────────────────┐
  │  get_order   │ ─────────────────────────────▶│  returns: status,   │
  └──────────────┘                               │  amount=$129.99,    │
                                                 │  delivered_at=...   │
                                                 └──────────┬──────────┘
                                                            │ status=delivered
  ┌──────────────┐     email="alice@example.com"            │
  │ get_customer │ ─────────────────────────────▶ tier=gold │
  └──────────────┘                                          │
                                                            │
  ┌────────────────────────────┐    order_id="ORD-1001"     │
  │ check_refund_eligibility   │ ──────────────────────────▶│
  └────────────────────────────┘                            │
        │ eligible=true, amount=129.99                      │
        │                                                   │
        ▼   (agent dynamically injects amount)              │
  ┌────────────────┐  order_id="ORD-1001", amount=129.99    │
  │  issue_refund  │ ──────────────────────────────────────▶│
  └────────────────┘                                        │
        │ TXN-757062                                        │
        ▼                                                   │
  ┌────────────────┐  "Your refund of $129.99..."           │
  │  send_reply    │ ──────────────────────────────────────▶│
  └────────────────┘                                        │
        │                                                   │
        ▼                                                   │
     STATUS: resolved · confidence=0.83                     │
```

---

## 7. Audit Log Structure

```
logs/audit_log.json
  └── [ Array — one record per ticket ]
        └── {
              ticket_id         : "TKT-2001"
              classification    : { intent, urgency_score, priority, confidence }
              steps             : [
                { step_number, timestamp, type, description,
                  tool_name?,  tool_input?, tool_output?,
                  success, error?, duration_ms? }
              ]
              tools_used        : ["get_order", "get_customer", ...]
              final_action      : "resolved_refund_request"
              confidence        : 0.8333
              status            : "resolved" | "escalated" | "failed"
              started_at        : ISO 8601
              completed_at      : ISO 8601
              total_duration_ms : 1003.46
            }

  Step types:
    observe  — ticket ingestion or classification result
    plan     — action planning decision or THINK reasoning step
    act      — tool execution (success or failure attempt)
    reflect  — post-tool confidence update
    resolve  — terminal: ticket resolved
    escalate — terminal: ticket sent to human queue
```
