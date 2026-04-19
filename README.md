# 🤖 Autonomous Support Resolution Agent

## 🌐 Live Demo

You can test the autonomous support agent here:

👉 [Open Live App](https://nenavath-chandu--hackathon2026-nenavath-chandu-app-dhrl4h.streamlit.app/)

Upload a tickets.json file and click "Run Autonomous" to see the agent in action.

## 🎥 Demo Video

A short demo showing:
- Autonomous ticket processing
- ReAct reasoning (Think → Act → Reflect)
- Tool chaining and failure handling

👉 [Watch Demo Video](https://youtu.be/XlrYmM6pUY8)

> **Hackathon Project** — A production-ready AI agent that reads customer support tickets, reasons about them autonomously, takes multi-step actions using real tools, and resolves or escalates — without any human in the loop.

---

## 🧩 Problem Statement

Support teams are flooded with repetitive tickets:

> *"Where's my order?"*  *"Can I get a refund?"*  *"My device won't connect."*

Most of these can be resolved instantly if an intelligent system could:
1. **Understand** what the customer actually wants
2. **Look up** the relevant data (order, customer, policy)
3. **Take action** (issue refund, send reply, escalate)
4. **Explain** every decision it made

That's exactly what this agent does — autonomously, concurrently, and with full audit trails.

---

## ✨ Key Features

| Feature | How It Works |
|---|---|
| 🧠 **Autonomous ReAct Reasoning** | Think → Act → Observe → Reflect loop — no single-step responses |
| 🔗 **Multi-Tool Chaining** | Each ticket triggers 3–5 sequential tool calls with data flowing between them |
| ⚡ **Async Concurrency** | All tickets processed in parallel via `asyncio` + `Semaphore` |
| 🔁 **Retry + Exponential Backoff** | Failed tools retried up to 2× with 0.5s → 1.0s delays |
| 📊 **Confidence Scoring** | Every decision scored 0.0–1.0; low confidence → auto-escalate |
| 🚨 **Smart Escalation** | Escalates on low confidence, unsafe intent, or exhausted retries |
| 🧾 **Full Audit Logging** | Every reasoning step, tool call, and decision saved to `audit_log.json` |
| 🛡️ **Failure-Aware** | Tools randomly fail (20%); system retries, adapts, and never crashes |

---

## 🚀 Quick Start

**No dependencies. Pure Python 3.9+.**

```bash
cd agent_system
python main.py
```

### CLI Options

```bash
# Use a different tickets file
python main.py --tickets data/tickets.json

# Limit concurrent processing to 3 tickets at a time
python main.py --max-concurrent 3

# Set simulated tool failure rate (0 = no failures, 1 = always fails)
python main.py --failure-rate 0.0

# Stress test — high failure rate to observe retry + escalation behaviour
python main.py --failure-rate 0.5
```

---

## 📁 Project Structure

```
agent_system/
│
├── main.py           ← Entry point: CLI, async orchestration, summary report
├── agent.py          ← Core ReAct loop: Observe → Classify → Plan → Act → Reflect
├── planner.py        ← Ticket classifier + action planner + confidence scorer
├── executor.py       ← Tool runner: retry logic, backoff, structured error handling
├── tools.py          ← 8 mock tools (READ + WRITE) with realistic failure simulation
├── logger.py         ← Audit system: per-step logging, Windows-safe JSON persistence
├── config.py         ← All thresholds, limits, retry settings, intent→tool pipelines
│
├── data/
│   └── tickets.json  ← 10 diverse real-world input tickets
│
├── logs/
│   └── audit_log.json  ← Generated at runtime: full structured audit trail
│
├── utils/
│   └── helpers.py    ← Ticket loader/validator, report generator, JSON utilities
│
├── README.md         ← You are here
├── architecture.md   ← Detailed system diagrams
└── failure_modes.md  ← How the system handles failures
```

---

## 🧠 How the Agent Thinks (ReAct Loop)

Every ticket goes through this reasoning cycle:

```
  TICKET IN
      │
      ▼
  1. OBSERVE   → Read ticket, extract fields, sanitize data
      │
      ▼
  2. CLASSIFY  → Score intent (7 types), urgency (0–1), confidence (0–1)
      │
      ▼
  3. PLAN      → Build ordered tool sequence based on intent
      │
      ▼
  4. ┌─────────────────────────────────┐
     │         ReAct LOOP              │
     │                                 │
     │  THINK  → "Why am I calling     │
     │           this tool?"           │
     │     ↓                           │
     │  ACT    → Call tool (w/ retry)  │
     │     ↓                           │
     │  OBSERVE → Capture result       │
     │     ↓                           │
     │  REFLECT → Update confidence,   │
     │            adjust plan          │
     └──────────────┬──────────────────┘
                    │
        ┌───────────┴──────────┐
        │                      │
        ▼                      ▼
    RESOLVE                ESCALATE
  (send_reply)         (human queue)
```

---

## 🔧 Tools Implemented

### READ Tools (gather information)
| Tool | Purpose |
|---|---|
| `get_order(order_id)` | Fetch order status, amount, product |
| `get_customer(email)` | Get customer tier, history, lifetime value |
| `get_product(product_id)` | Check product details and return window |
| `search_knowledge_base(query)` | Find matching policy/FAQ articles |

### WRITE Tools (take action)
| Tool | Purpose |
|---|---|
| `check_refund_eligibility(order_id)` | Evaluate if order qualifies for refund |
| `issue_refund(order_id, amount)` | Process the refund and get transaction ID |
| `send_reply(ticket_id, message)` | Send resolution email to customer |
| `escalate(ticket_id, summary, priority)` | Route to human agent queue |

Each tool has a **20% chance of failure** to simulate real-world conditions. The Executor handles retries automatically.

---

## 📊 Confidence-Based Decision Making

```
  Confidence Scale
  ──────────────────────────────────────────
  0.0       0.50        0.75        1.0
   │──────────│───────────│───────────│
   │ ALWAYS   │CONDITIONAL│AUTO-RESOLVE│
   │ESCALATE  │(by intent)│  (high    │
   │          │           │confidence)│
  ──────────────────────────────────────────

  Confidence changes dynamically:
    • Intent match strength      → initial score
    • Resolvable intent          → +10%
    • Tool failure (exhausted)   → −20%
    • Tool result missing data   → −15%
```

---

## 🔁 Retry Strategy

```
  Tool Called
       │
       ├── Success → Return result ✓
       │
       ├── Failure → Wait 0.5s → Retry #1
       │
       ├── Failure → Wait 1.0s → Retry #2
       │
       └── Failure → ToolExecutionError
                          │
                          └── Confidence −20%
                               If conf < 50% → ESCALATE
```

---

## 📄 Sample Audit Log Record

```json
{
  "ticket_id": "TKT-2003",
  "classification": {
    "intent": "refund_request",
    "urgency_score": 1.0,
    "priority": "critical",
    "confidence": 0.8333
  },
  "steps": [
    { "step_number": 1, "type": "observe",  "description": "Ingested ticket" },
    { "step_number": 4, "type": "plan",     "description": "[Think] Will call 'get_order' — Fetch order details" },
    { "step_number": 5, "type": "act",      "tool_name": "get_order",  "duration_ms": 146.9, "success": true },
    { "step_number": 6, "type": "reflect",  "description": "Confidence=0.83 after get_order" },
    { "step_number": 11,"type": "act",      "tool_name": "check_refund_eligibility", "success": true },
    { "step_number": 14,"type": "act",      "tool_name": "issue_refund", "success": true },
    { "step_number": 19,"type": "act",      "tool_name": "send_reply",  "success": true },
    { "step_number": 20,"type": "resolve",  "description": "Ticket resolved with confidence=0.83" }
  ],
  "tools_used": ["get_order", "get_customer", "check_refund_eligibility", "issue_refund", "send_reply"],
  "final_action": "resolved_refund_request",
  "confidence": 0.8333,
  "status": "resolved",
  "total_duration_ms": 1003.46
}
```

---

## ⚙️ Configuration Reference

Edit `config.py` to tune all system behaviour:

| Setting | Default | Effect |
|---|---|---|
| `CONFIDENCE_ESCALATION_THRESHOLD` | `0.50` | Below this score → always escalate |
| `CONFIDENCE_AUTO_RESOLVE_THRESHOLD` | `0.75` | Above this score → auto-resolve |
| `MAX_RETRIES` | `2` | Max retry attempts per tool call |
| `RETRY_BASE_DELAY` | `0.5s` | Initial backoff delay |
| `RETRY_BACKOFF_FACTOR` | `2.0` | Exponential multiplier (0.5s, 1.0s) |
| `TOOL_FAILURE_RATE` | `0.20` | Simulated failure probability per call |
| `MAX_CONCURRENT_TICKETS` | `10` | Semaphore cap on parallel processing |
| `MAX_REASONING_STEPS` | `10` | Safety cap on the ReAct loop |
| `MIN_TOOL_CALLS` | `3` | Minimum tools guaranteed per ticket |

---

## 🏗️ Design Decisions

### Why ReAct over a simple decision tree?
ReAct allows the agent to **adapt mid-execution**. If `check_refund_eligibility` returns `eligible=true`, the agent dynamically updates the refund amount in the `issue_refund` call — something a static decision tree cannot do.

### Why asyncio over threading?
All tool calls are I/O-bound (network calls in production). `asyncio` handles thousands of concurrent tickets with a single thread, zero lock overhead, and predictable cancellation.

### Why rule-based classification?
Keeps the system fully explainable and dependency-free. In production, swap `Classifier.classify()` with an LLM API call — the rest of the architecture is unchanged.

---

## 📚 Further Reading

- [`architecture.md`](architecture.md) — Full system diagrams
- [`failure_modes.md`](failure_modes.md) — How the system handles every failure scenario
- [`data/tickets.json`](data/tickets.json) — Sample input tickets
- [`logs/audit_log.json`](logs/audit_log.json) — Generated audit trail
