"""
tools.py - All tool implementations for the Support Resolution Agent.

Each tool is an async function that:
  • Accepts typed arguments
  • Returns structured data (dict)
  • Simulates realistic random failures
  • Logs its own usage to the console logger

Tool categories
---------------
READ  : get_order, get_customer, get_product, search_knowledge_base
WRITE : check_refund_eligibility, issue_refund, send_reply, escalate
"""

import asyncio
import random
import time
from typing import Any, Dict

from config import agent_config
from logger import console_log

# ---------------------------------------------------------------------------
# Fake data stores — realistic mock data
# ---------------------------------------------------------------------------

_ORDERS: Dict[str, Dict[str, Any]] = {
    "ORD-1001": {
        "order_id": "ORD-1001",
        "customer_email": "alice@example.com",
        "product_id": "PRD-55",
        "amount": 129.99,
        "status": "delivered",
        "delivered_at": "2026-04-10",
        "payment_method": "credit_card",
    },
    "ORD-1002": {
        "order_id": "ORD-1002",
        "customer_email": "bob@example.com",
        "product_id": "PRD-77",
        "amount": 49.99,
        "status": "in_transit",
        "delivered_at": None,
        "payment_method": "paypal",
    },
    "ORD-1003": {
        "order_id": "ORD-1003",
        "customer_email": "carol@example.com",
        "product_id": "PRD-21",
        "amount": 299.00,
        "status": "cancelled",
        "delivered_at": None,
        "payment_method": "credit_card",
    },
    "ORD-1004": {
        "order_id": "ORD-1004",
        "customer_email": "dave@example.com",
        "product_id": "PRD-88",
        "amount": 19.99,
        "status": "delivered",
        "delivered_at": "2026-03-28",
        "payment_method": "debit_card",
    },
    "ORD-1005": {
        "order_id": "ORD-1005",
        "customer_email": "eve@example.com",
        "product_id": "PRD-33",
        "amount": 85.00,
        "status": "delivered",
        "delivered_at": "2026-04-15",
        "payment_method": "credit_card",
    },
}

_CUSTOMERS: Dict[str, Dict[str, Any]] = {
    "alice@example.com": {
        "email": "alice@example.com",
        "name": "Alice Chen",
        "tier": "gold",
        "account_age_days": 720,
        "lifetime_value": 1240.50,
        "previous_refunds": 1,
    },
    "bob@example.com": {
        "email": "bob@example.com",
        "name": "Bob Martinez",
        "tier": "silver",
        "account_age_days": 180,
        "lifetime_value": 310.00,
        "previous_refunds": 0,
    },
    "carol@example.com": {
        "email": "carol@example.com",
        "name": "Carol Smith",
        "tier": "platinum",
        "account_age_days": 1450,
        "lifetime_value": 5870.00,
        "previous_refunds": 2,
    },
    "dave@example.com": {
        "email": "dave@example.com",
        "name": "Dave Johnson",
        "tier": "bronze",
        "account_age_days": 45,
        "lifetime_value": 65.00,
        "previous_refunds": 0,
    },
    "eve@example.com": {
        "email": "eve@example.com",
        "name": "Eve Williams",
        "tier": "silver",
        "account_age_days": 365,
        "lifetime_value": 540.00,
        "previous_refunds": 1,
    },
    "unknown@example.com": {
        "email": "unknown@example.com",
        "name": "Unknown User",
        "tier": "bronze",
        "account_age_days": 0,
        "lifetime_value": 0.0,
        "previous_refunds": 0,
    },
}

_PRODUCTS: Dict[str, Dict[str, Any]] = {
    "PRD-55": {
        "product_id": "PRD-55",
        "name": "Wireless Noise-Cancelling Headphones",
        "category": "electronics",
        "price": 129.99,
        "return_window_days": 30,
        "in_stock": True,
        "rating": 4.5,
    },
    "PRD-77": {
        "product_id": "PRD-77",
        "name": "Premium Leather Wallet",
        "category": "accessories",
        "price": 49.99,
        "return_window_days": 14,
        "in_stock": True,
        "rating": 4.2,
    },
    "PRD-21": {
        "product_id": "PRD-21",
        "name": "Smart Home Hub",
        "category": "electronics",
        "price": 299.00,
        "return_window_days": 30,
        "in_stock": False,
        "rating": 3.8,
    },
    "PRD-88": {
        "product_id": "PRD-88",
        "name": "Yoga Mat Pro",
        "category": "sports",
        "price": 19.99,
        "return_window_days": 7,
        "in_stock": True,
        "rating": 4.7,
    },
    "PRD-33": {
        "product_id": "PRD-33",
        "name": "Ergonomic Office Chair",
        "category": "furniture",
        "price": 85.00,
        "return_window_days": 30,
        "in_stock": True,
        "rating": 4.6,
    },
}

_KNOWLEDGE_BASE = [
    {
        "id": "KB-001",
        "topic": "refund policy",
        "content": "Full refunds are issued within 30 days of delivery for electronics and furniture. "
                   "Accessories have a 14-day window. Yoga mats have a 7-day window. "
                   "Refunds are processed within 5-7 business days.",
    },
    {
        "id": "KB-002",
        "topic": "order tracking",
        "content": "Orders in 'in_transit' status can be tracked via the carrier portal. "
                   "Standard delivery takes 3-5 business days. Express takes 1-2 days.",
    },
    {
        "id": "KB-003",
        "topic": "technical support",
        "content": "For device setup issues, restart the device and ensure firmware is updated. "
                   "Visit support.example.com/setup for step-by-step guides.",
    },
    {
        "id": "KB-004",
        "topic": "account management",
        "content": "Password resets can be done via the login page. "
                   "Account freezes require identity verification at verify.example.com.",
    },
    {
        "id": "KB-005",
        "topic": "payment issues",
        "content": "Duplicate charges are investigated within 24 hours. "
                   "Contact billing@example.com with your order ID for expedited review.",
    },
    {
        "id": "KB-006",
        "topic": "product complaints",
        "content": "Defective products within warranty are replaced free of charge. "
                   "Provide photos of the defect to support@example.com.",
    },
]

# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

async def _simulate_latency(min_ms: float = 50, max_ms: float = 300) -> None:
    """Non-blocking simulated network/IO latency."""
    delay = random.uniform(min_ms, max_ms) / 1000
    await asyncio.sleep(delay)


def _maybe_fail(tool_name: str) -> None:
    """Raise a RuntimeError with ~TOOL_FAILURE_RATE probability."""
    if random.random() < agent_config.tool_failure_rate:
        raise RuntimeError(
            f"Tool '{tool_name}' failed: simulated transient error (network timeout / service unavailable)"
        )


# ===========================================================================
# READ TOOLS
# ===========================================================================

async def get_order(order_id: str) -> Dict[str, Any]:
    """
    Retrieve order details by order ID.
    Raises RuntimeError ~20 % of the time to simulate API failure.
    """
    t0 = time.monotonic()
    console_log.debug("[TOOL] get_order called with order_id=%s", order_id)
    await _simulate_latency()
    _maybe_fail("get_order")

    order = _ORDERS.get(order_id)
    if not order:
        return {
            "success": False,
            "error": f"Order '{order_id}' not found",
            "order_id": order_id,
        }
    result = {"success": True, **order}
    console_log.debug("[TOOL] get_order → %s (%.0fms)", order_id, (time.monotonic() - t0) * 1000)
    return result


async def get_customer(email: str) -> Dict[str, Any]:
    """
    Retrieve customer profile by email address.
    """
    t0 = time.monotonic()
    console_log.debug("[TOOL] get_customer called with email=%s", email)
    await _simulate_latency()
    _maybe_fail("get_customer")

    customer = _CUSTOMERS.get(email, _CUSTOMERS["unknown@example.com"])
    result = {"success": True, **customer}
    console_log.debug("[TOOL] get_customer → %s (%.0fms)", email, (time.monotonic() - t0) * 1000)
    return result


async def get_product(product_id: str) -> Dict[str, Any]:
    """
    Retrieve product information by product ID.
    """
    t0 = time.monotonic()
    console_log.debug("[TOOL] get_product called with product_id=%s", product_id)
    await _simulate_latency()
    _maybe_fail("get_product")

    product = _PRODUCTS.get(product_id)
    if not product:
        return {
            "success": False,
            "error": f"Product '{product_id}' not found",
            "product_id": product_id,
        }
    result = {"success": True, **product}
    console_log.debug("[TOOL] get_product → %s (%.0fms)", product_id, (time.monotonic() - t0) * 1000)
    return result


async def search_knowledge_base(query: str) -> Dict[str, Any]:
    """
    Fuzzy keyword search across knowledge-base articles.
    Returns top-3 matching articles.
    """
    t0 = time.monotonic()
    console_log.debug("[TOOL] search_knowledge_base called with query=%r", query)
    await _simulate_latency(80, 400)
    _maybe_fail("search_knowledge_base")

    query_lower = query.lower()
    scored = []
    for article in _KNOWLEDGE_BASE:
        score = sum(
            1
            for word in query_lower.split()
            if word in article["topic"] or word in article["content"]
        )
        if score > 0:
            scored.append((score, article))

    scored.sort(key=lambda x: x[0], reverse=True)
    results = [art for _, art in scored[:3]]
    console_log.debug(
        "[TOOL] search_knowledge_base → %d results (%.0fms)",
        len(results),
        (time.monotonic() - t0) * 1000,
    )
    return {
        "success": True,
        "query": query,
        "results": results,
        "total_found": len(results),
    }


# ===========================================================================
# WRITE TOOLS
# ===========================================================================

async def check_refund_eligibility(order_id: str) -> Dict[str, Any]:
    """
    Evaluate whether an order qualifies for a refund.
    Checks order status, delivery date, and product return window.
    """
    t0 = time.monotonic()
    console_log.debug("[TOOL] check_refund_eligibility called with order_id=%s", order_id)
    await _simulate_latency(100, 350)
    _maybe_fail("check_refund_eligibility")

    order = _ORDERS.get(order_id)
    if not order:
        return {
            "success": False,
            "eligible": False,
            "reason": f"Order '{order_id}' not found",
        }

    if order["status"] == "cancelled":
        return {
            "success": True,
            "eligible": True,
            "reason": "Order was cancelled — full refund eligible",
            "amount": order["amount"],
        }

    if order["status"] == "in_transit":
        return {
            "success": True,
            "eligible": False,
            "reason": "Order still in transit — not yet eligible for refund",
        }

    # delivered — check return window
    product = _PRODUCTS.get(order.get("product_id", ""))
    if not product:
        return {
            "success": True,
            "eligible": False,
            "reason": "Product data unavailable; cannot determine return window",
        }

    from datetime import date
    delivered = date.fromisoformat(order["delivered_at"])
    days_since = (date.today() - delivered).days
    window = product["return_window_days"]

    eligible = days_since <= window
    result = {
        "success": True,
        "eligible": eligible,
        "order_id": order_id,
        "days_since_delivery": days_since,
        "return_window_days": window,
        "amount": order["amount"] if eligible else 0.0,
        "reason": (
            f"Within {window}-day return window ({days_since} days since delivery)"
            if eligible
            else f"Outside {window}-day return window ({days_since} days since delivery)"
        ),
    }
    console_log.debug("[TOOL] check_refund_eligibility → eligible=%s (%.0fms)", eligible, (time.monotonic() - t0) * 1000)
    return result


async def issue_refund(order_id: str, amount: float) -> Dict[str, Any]:
    """
    Issue a monetary refund for an order.
    Generates a mock transaction ID and confirmation.
    """
    t0 = time.monotonic()
    console_log.debug("[TOOL] issue_refund called order_id=%s amount=%.2f", order_id, amount)
    await _simulate_latency(150, 500)
    _maybe_fail("issue_refund")

    if amount <= 0:
        return {
            "success": False,
            "error": f"Invalid refund amount: {amount}",
        }

    transaction_id = f"TXN-{random.randint(100000, 999999)}"
    result = {
        "success": True,
        "order_id": order_id,
        "refund_amount": round(amount, 2),
        "transaction_id": transaction_id,
        "processing_days": random.randint(3, 7),
        "message": f"Refund of ${amount:.2f} initiated. Transaction ID: {transaction_id}",
    }
    console_log.debug("[TOOL] issue_refund → txn=%s (%.0fms)", transaction_id, (time.monotonic() - t0) * 1000)
    return result


async def send_reply(ticket_id: str, message: str) -> Dict[str, Any]:
    """
    Send a reply message to the customer for a given ticket.
    """
    t0 = time.monotonic()
    console_log.debug("[TOOL] send_reply called ticket_id=%s", ticket_id)
    await _simulate_latency(50, 200)
    _maybe_fail("send_reply")

    result = {
        "success": True,
        "ticket_id": ticket_id,
        "message_sent": message[:200] + ("..." if len(message) > 200 else ""),
        "delivery_status": "queued",
        "channel": "email",
    }
    console_log.debug("[TOOL] send_reply → delivered (%.0fms)", (time.monotonic() - t0) * 1000)
    return result


async def escalate(ticket_id: str, summary: str, priority: str = "medium") -> Dict[str, Any]:
    """
    Escalate a ticket to a human agent with a priority level.
    """
    t0 = time.monotonic()
    console_log.debug("[TOOL] escalate called ticket_id=%s priority=%s", ticket_id, priority)
    await _simulate_latency(50, 150)
    _maybe_fail("escalate")

    valid_priorities = {"low", "medium", "high", "critical"}
    if priority not in valid_priorities:
        priority = "medium"

    escalation_id = f"ESC-{random.randint(10000, 99999)}"
    result = {
        "success": True,
        "ticket_id": ticket_id,
        "escalation_id": escalation_id,
        "priority": priority,
        "summary": summary[:300],
        "assigned_queue": f"support-{priority}",
        "message": f"Ticket escalated. Escalation ID: {escalation_id}. "
                   f"A human agent will respond within SLA for priority '{priority}'.",
    }
    console_log.debug("[TOOL] escalate → esc_id=%s (%.0fms)", escalation_id, (time.monotonic() - t0) * 1000)
    return result


# ---------------------------------------------------------------------------
# Tool registry — maps string names to callables (used by executor)
# ---------------------------------------------------------------------------
TOOL_REGISTRY = {
    "get_order": get_order,
    "get_customer": get_customer,
    "get_product": get_product,
    "search_knowledge_base": search_knowledge_base,
    "check_refund_eligibility": check_refund_eligibility,
    "issue_refund": issue_refund,
    "send_reply": send_reply,
    "escalate": escalate,
}
