# Failure Modes & Resilience — Autonomous Support Resolution Agent

This document explains every failure scenario the system can encounter and precisely how it handles each one. This is a key design document for reviewers and interviewers.

---

## Failure Mode 1: Tool Timeout / Transient Network Error

### Scenario
A tool call (e.g., `get_order`) fails mid-execution due to a simulated network timeout or service unavailability.

### How It Happens
Every tool has a **20% random failure rate** (configurable via `--failure-rate`). In production this models real-world API instability.

```python
# tools.py — _maybe_fail()
def _maybe_fail(tool_name: str) -> None:
    if random.random() < agent_config.tool_failure_rate:
        raise RuntimeError(
            f"Tool '{tool_name}' failed: simulated transient error"
        )
```

### System Response

```
get_order called
      │
      └── RuntimeError raised (20% chance)
                │
                ▼
          Executor catches exception
                │
                ├── Logs: "Tool 'get_order' failed (attempt 1)"
                ├── AuditEntry step recorded: success=False
                ├── Waits 0.5s  (exponential backoff)
                │
                ▼
          Retry #1
                │
                └── Success → normal flow continues ✓
                │
                └── Failure → wait 1.0s → Retry #2
                                  │
                                  └── Success ✓
                                  │
                                  └── ToolExecutionError raised
```

### Guaranteed Behaviour After All Retries Fail
1. `ToolExecutionError` is raised by `executor.py`
2. `agent.py` catches it and records the failure in the audit log
3. Confidence is reduced by **−0.20**
4. Agent continues with the remaining plan steps
5. If confidence drops below **0.50** → ticket is auto-escalated

### Audit Trail
```json
{
  "type": "act",
  "tool_name": "get_order",
  "success": false,
  "error": "Tool 'get_order' failed after 3 attempt(s): simulated transient error",
  "duration_ms": 62.3
},
{
  "type": "reflect",
  "description": "Tool 'get_order' failed after 3 attempts — adjusting plan",
  "success": false
}
```

---

## Failure Mode 2: Missing or Invalid Ticket Data

### Scenario
A ticket arrives with missing fields (no `order_id`, no `customer_email`, malformed structure).

### How It Happens
Input JSON may be hand-crafted, scraped, or from an external system with schema mismatches.

### Layer 1: Pre-processing Validation (`utils/helpers.py`)

```python
def _validate_ticket(ticket, index) -> List[str]:
    issues = []
    if not isinstance(ticket, dict):
        issues.append("ticket is not an object")
    if "ticket_id" not in ticket:
        issues.append("missing 'ticket_id'")
    if not any(k in ticket for k in ("subject", "description", "body", "message")):
        issues.append("missing body text")
    return issues
```

Invalid tickets are **logged and skipped** before they ever reach the agent:
```
WARNING | Ticket #3 skipped — validation issues: ['missing ticket_id']
```

### Layer 2: Safe Argument Resolution (`planner.py`)

If a valid ticket is missing optional fields like `order_id` or `product_id`, the Planner handles it gracefully:

```python
# _build_args() — returns None if required arg is missing
"get_order": {"order_id": order_id} if order_id else None,
```

When `None` is returned, that tool is **skipped from the plan entirely**. The plan adapts to what data is available.

### Layer 3: Default Fallback Values

```python
# Falls back to unknown@example.com if no email present
email = ticket.get("customer_email", ticket.get("email", "unknown@example.com"))

# Falls back to medium priority if missing
priority = classification.get("priority", "medium")
```

### Layer 4: Unknown Intent Handling

If no keywords match, classification returns `intent="unknown"` with `confidence=0.40`:
- The **unknown** pipeline still runs: `get_customer → search_knowledge_base → escalate`
- Low initial confidence (0.40 < 0.50 threshold) → ticket is immediately escalated to a human

### Result
No crash. Every ticket, no matter how malformed, either:
- Gets skipped at validation with a logged warning, OR
- Gets processed safely with a gracefully degraded plan, OR
- Gets escalated to a human agent with a full audit trail

---

## Failure Mode 3: Low Confidence — Cannot Safely Resolve

### Scenario
The agent processes a ticket but cannot reach sufficient confidence to act autonomously. This happens when:
- The intent is ambiguous (complaint mixed with refund request)
- Multiple tools fail, degrading confidence
- The ticket contains an always-escalate intent (complaint, unknown)

### Confidence Degradation Chain

```
  Initial classification:  complaint → conf = 0.73

  Tool "search_knowledge_base" succeeds:  conf = 0.73 (unchanged on success)

  Tool "escalate" fails on attempt 1:
    → wait 0.5s → retry
    → attempt 2 succeeds: conf = 0.73

  But intent = "complaint" → ALWAYS escalate regardless of confidence:
    conf = 0.73  (above 0.50, but rules say escalate)
```

```
  More extreme example (multiple tool failures):

  initial conf = 0.83
  get_order fails all retries:         conf = 0.83 - 0.20 = 0.63
  check_refund_eligibility fails:      conf = 0.63 - 0.20 = 0.43

  0.43 < 0.50 threshold → ESCALATE IMMEDIATELY
```

### Decision Gate in `planner.py`

```python
# decide_next_action() — called after every tool execution

if step_index >= len(plan):
    if confidence < agent_config.confidence_escalation_threshold:  # 0.50
        return {
            "action": "escalate",
            "reason": f"Low confidence ({confidence:.2f}) after full reasoning chain",
        }
    if intent in ESCALATE_INTENTS:  # {"complaint", "unknown"}
        return {
            "action": "escalate",
            "reason": f"Intent '{intent}' always routed to human review",
        }
    return {"action": "resolve", ...}
```

### What the Escalation Includes

The escalation message passed to the human agent contains:
```
Intent: complaint | Urgency: 0.90 | Confidence: 0.43 | Reason: Low confidence...
```

This gives the human agent full context on:
- What the customer originally wanted
- Why the system couldn't resolve it
- How urgent the ticket is
- What tools were already attempted

### Audit Trail
```json
{
  "type": "escalate",
  "description": "Escalated: Low confidence (0.43) after full reasoning chain",
  "tool_output": {
    "escalation_id": "ESC-32277",
    "priority": "high",
    "assigned_queue": "support-high"
  }
},
{
  "final_action": "escalated_Low confidence (0.43) after full reasoning chain",
  "confidence": 0.43,
  "status": "escalated"
}
```

---

## Failure Mode 4: Audit Log Write Error (Windows File Locking)

### Scenario
On Windows, antivirus software or VS Code may hold a file lock on `audit_log.json` at the exact moment the agent tries to write it.

### System Response (`logger.py`)

```
  write → audit_log.json.tmp     (file handle explicitly closed)
      │
      ├── os.remove(audit_log.json)  if it exists  (avoids rename-over-open-file)
      │
      ├── os.rename(.tmp → .json)
      │
      └── PermissionError?
               │
               ├── wait 0.1s → retry #1
               ├── wait 0.2s → retry #2
               └── retry #3 → if still fails:
                      log error
                      clean up .tmp file
                      data stays in memory (no loss)
```

**Critical guarantee:** Data is **never lost**. Even if the file cannot be written, all audit records remain in `self._records` in memory for the duration of the process.

---

## Failure Mode 5: Agent Loop Crash (Unhandled Exception)

### Scenario
An unexpected Python exception occurs inside the ReAct loop (e.g., `AttributeError`, `KeyError` from unexpected tool output shape).

### System Response (`agent.py`)

```python
try:
    await self._run_react_loop(ticket, entry)
except Exception as exc:
    # Catches ANY exception — the loop can never crash the process
    entry.add_step(
        step_type="reflect",
        description=f"Agent loop crashed: {exc}",
        success=False,
        error=traceback.format_exc(),   # full stack trace saved to audit log
    )
    entry.finalize(
        final_action="escalated_due_to_system_error",
        confidence=0.0,
        status="failed",
    )

# Audit log is ALWAYS committed, even on crash
await audit_logger.commit(entry)
```

**Result:** The ticket is marked `status="failed"` with the full stack trace in the audit log. **No other tickets are affected.** The crash is isolated to that one asyncio task.

---

## Summary Table

| Failure | Triggered By | System Response | Final Status |
|---|---|---|---|
| Tool transient error | 20% random failure | Retry × 2 with backoff | resolved or escalated |
| Tool exhausted retries | 3× failure | confidence −20%, continue with plan | escalated if conf < 0.50 |
| Missing ticket fields | Bad input data | Skip tool / use defaults | resolved or escalated |
| Invalid ticket schema | Missing ticket_id | Skip ticket entirely (validation) | N/A (not processed) |
| Low confidence | Ambiguous intent | Auto-escalate with full context | escalated |
| Unsafe intent (complaint) | Intent policy | Force escalate via routing rules | escalated |
| Max reasoning steps | Loop safety cap | Emergency escalate | escalated |
| Audit log locked | Windows file lock | Retry × 3, data stays in memory | N/A (no data loss) |
| Agent loop crash | Unexpected exception | Full stack trace saved, ticket failed | failed |
