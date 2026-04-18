"""
utils/helpers.py - Shared utility functions used across the agent system.

Includes:
  • Ticket loader / validator
  • Pretty-printing helpers
  • Summary report generator
  • JSON serialization utilities
"""

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Ticket loading
# ---------------------------------------------------------------------------

def load_tickets(filepath: str) -> List[Dict[str, Any]]:
    """
    Load and validate tickets from a JSON file.
    Returns only tickets that pass basic schema validation.
    Skips malformed entries with a warning.
    """
    from logger import console_log

    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Tickets file not found: {filepath}")

    with open(filepath, "r", encoding="utf-8") as fh:
        try:
            raw = json.load(fh)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in tickets file: {exc}") from exc

    if not isinstance(raw, list):
        raise ValueError("Tickets file must contain a JSON array at the top level")

    valid_tickets = []
    for i, ticket in enumerate(raw):
        issues = _validate_ticket(ticket, index=i)
        if issues:
            console_log.warning("Ticket #%d skipped — validation issues: %s", i, issues)
        else:
            valid_tickets.append(ticket)

    console_log.info("Loaded %d/%d valid tickets from %s", len(valid_tickets), len(raw), filepath)
    return valid_tickets


def _validate_ticket(ticket: Any, index: int) -> List[str]:
    """Return list of validation issues (empty list means valid)."""
    issues = []
    if not isinstance(ticket, dict):
        issues.append("ticket is not an object")
        return issues
    if "ticket_id" not in ticket:
        issues.append("missing 'ticket_id'")
    if not any(k in ticket for k in ("subject", "description", "body", "message")):
        issues.append("missing at least one of: subject, description, body, message")
    return issues


# ---------------------------------------------------------------------------
# Pretty-print helpers
# ---------------------------------------------------------------------------

def format_duration(ms: Optional[float]) -> str:
    """Format milliseconds as a human-readable string."""
    if ms is None:
        return "N/A"
    if ms < 1000:
        return f"{ms:.0f}ms"
    return f"{ms / 1000:.2f}s"


def print_banner(text: str, width: int = 70) -> None:
    """Print a highlighted banner to stdout."""
    border = "═" * width
    padded = text.center(width)
    print(f"\n╔{border}╗\n║{padded}║\n╚{border}╝\n")


def print_section(title: str, width: int = 70) -> None:
    print(f"\n{'─' * width}")
    print(f"  {title}")
    print(f"{'─' * width}")


# ---------------------------------------------------------------------------
# Summary report
# ---------------------------------------------------------------------------

def generate_summary_report(summary: Dict[str, Any], audit_records: List[Dict[str, Any]]) -> str:
    """
    Build a human-readable text report from the aggregate summary dict
    and the raw audit records.
    """
    lines = [
        "",
        "╔══════════════════════════════════════════════════════════════════════╗",
        "║           AUTONOMOUS SUPPORT RESOLUTION AGENT — RUN SUMMARY         ║",
        "╚══════════════════════════════════════════════════════════════════════╝",
        "",
        f"  Total Tickets Processed : {summary.get('total_tickets', 0)}",
        f"  ✅ Resolved             : {summary.get('resolved', 0)}",
        f"  🚨 Escalated            : {summary.get('escalated', 0)}",
        f"  💥 Failed               : {summary.get('failed', 0)}",
        f"  Avg Confidence Score    : {summary.get('avg_confidence', 0):.2%}",
        f"  Avg Steps per Ticket   : {summary.get('avg_steps_per_ticket', 0):.1f}",
        "",
        "  ─── Per-Ticket Results ───────────────────────────────────────────",
    ]

    for rec in audit_records:
        status_icon = {
            "resolved": "✅",
            "escalated": "🚨",
            "failed": "💥",
        }.get(rec.get("status", ""), "❓")

        intent = rec.get("classification", {}).get("intent", "unknown")
        conf = rec.get("confidence", 0)
        tools = ", ".join(rec.get("tools_used", []))
        duration = format_duration(rec.get("total_duration_ms"))

        lines.append(
            f"  {status_icon} [{rec.get('ticket_id', '?')}] "
            f"intent={intent} conf={conf:.0%} tools=[{tools}] ⏱ {duration}"
        )

    lines += [
        "",
        f"  Generated at: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# JSON utilities
# ---------------------------------------------------------------------------

def safe_json_dumps(obj: Any, indent: int = 2) -> str:
    """Serialize obj to JSON, handling non-serializable types gracefully."""
    return json.dumps(obj, indent=indent, default=_json_default, ensure_ascii=False)


def _json_default(obj: Any) -> Any:
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return str(obj)
