"""
config.py - Central configuration for the Autonomous Support Resolution Agent.
All thresholds, retry limits, timeouts, and system-level settings live here.
"""

import os
from dataclasses import dataclass, field
from typing import Dict


# ---------------------------------------------------------------------------
# Confidence thresholds
# ---------------------------------------------------------------------------
CONFIDENCE_ESCALATION_THRESHOLD = 0.50   # Escalate if confidence < 50 %
CONFIDENCE_AUTO_RESOLVE_THRESHOLD = 0.75  # Auto-resolve if confidence >= 75 %

# ---------------------------------------------------------------------------
# Retry / back-off settings
# ---------------------------------------------------------------------------
MAX_RETRIES = 2                 # Max retry attempts per tool call
RETRY_BASE_DELAY = 0.5          # Initial back-off delay in seconds
RETRY_BACKOFF_FACTOR = 2.0      # Exponential multiplier

# ---------------------------------------------------------------------------
# Agent reasoning loop
# ---------------------------------------------------------------------------
MAX_REASONING_STEPS = 10        # Safety cap: stop after N steps
MIN_TOOL_CALLS = 3              # At least 3 tool calls per ticket chain

# ---------------------------------------------------------------------------
# Tool failure simulation
# ---------------------------------------------------------------------------
TOOL_FAILURE_RATE = 0.20        # 20 % random failure probability per call

# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------
MAX_CONCURRENT_TICKETS = 10     # Semaphore limit for async processing

# ---------------------------------------------------------------------------
# File paths
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
TICKETS_FILE = os.path.join(DATA_DIR, "tickets.json")
AUDIT_LOG_FILE = os.path.join(LOGS_DIR, "audit_log.json")

# ---------------------------------------------------------------------------
# Intent → Tool pipeline mappings
# Defines which tool sequence the planner should consider for each intent.
# ---------------------------------------------------------------------------
INTENT_TOOL_PIPELINE: Dict[str, list] = {
    "refund_request": [
        "get_order",
        "get_customer",
        "check_refund_eligibility",
        "issue_refund",
        "send_reply",
    ],
    "order_status": [
        "get_order",
        "get_customer",
        "search_knowledge_base",
        "send_reply",
    ],
    "product_inquiry": [
        "get_product",
        "search_knowledge_base",
        "send_reply",
    ],
    "technical_support": [
        "search_knowledge_base",
        "get_customer",
        "send_reply",
    ],
    "account_issue": [
        "get_customer",
        "search_knowledge_base",
        "send_reply",
    ],
    "complaint": [
        "get_customer",
        "get_order",
        "search_knowledge_base",
        "escalate",
    ],
    "unknown": [
        "get_customer",
        "search_knowledge_base",
        "escalate",
    ],
}

# ---------------------------------------------------------------------------
# Urgency scoring weights (used in urgency classifier)
# ---------------------------------------------------------------------------
URGENCY_KEYWORDS = {
    "urgent": 0.4,
    "asap": 0.4,
    "immediately": 0.35,
    "refund": 0.25,
    "broken": 0.20,
    "error": 0.15,
    "not working": 0.20,
    "please": 0.05,
    "cancel": 0.15,
    "fraud": 0.45,
    "charge": 0.20,
    "wrong": 0.15,
}

# ---------------------------------------------------------------------------
# Resolvability heuristics
# ---------------------------------------------------------------------------
RESOLVABLE_INTENTS = {"refund_request", "order_status", "product_inquiry", "account_issue", "technical_support"}
ESCALATE_INTENTS = {"complaint", "unknown"}

@dataclass
class AgentConfig:
    """Typed config object used throughout the system."""
    confidence_escalation_threshold: float = CONFIDENCE_ESCALATION_THRESHOLD
    confidence_auto_resolve_threshold: float = CONFIDENCE_AUTO_RESOLVE_THRESHOLD
    max_retries: int = MAX_RETRIES
    retry_base_delay: float = RETRY_BASE_DELAY
    retry_backoff_factor: float = RETRY_BACKOFF_FACTOR
    max_reasoning_steps: int = MAX_REASONING_STEPS
    min_tool_calls: int = MIN_TOOL_CALLS
    tool_failure_rate: float = TOOL_FAILURE_RATE
    max_concurrent_tickets: int = MAX_CONCURRENT_TICKETS
    tickets_file: str = TICKETS_FILE
    audit_log_file: str = AUDIT_LOG_FILE
    intent_tool_pipeline: Dict[str, list] = field(default_factory=lambda: INTENT_TOOL_PIPELINE)

# Singleton used by all modules
agent_config = AgentConfig()
