"""
agent.py - Core ReAct-style agent loop for the Support Resolution Agent.

INTERVIEW TALKING POINT:
    This file is the "brain" of the system. It implements the ReAct pattern
    (Reasoning + Acting), which is a standard technique for building AI agents
    that can plan, act, observe results, and adapt — rather than just returning
    a single response.

ReAct Loop (per ticket)
-----------------------
1. OBSERVE  – Read and understand the ticket
2. CLASSIFY – Planner assigns intent, urgency, confidence
3. PLAN     – Planner builds an ordered tool sequence
4. LOOP:
   a. THINK  – Log the reasoning step (what and why)
   b. ACT    – Executor calls the tool (with retry)
   c. OBSERVE – Capture tool output
   d. REFLECT – Planner re-evaluates; decide next or stop

All reasoning steps and tool outputs are recorded in the AuditEntry,
giving complete explainability for every decision the agent makes.
"""

import asyncio
import traceback
from typing import Any, Dict, Optional

from config import agent_config
from executor import Executor, ToolExecutionError
from logger import AuditEntry, audit_logger, console_log
from planner import Planner, planner as global_planner


class SupportAgent:
    """
    Processes a single support ticket through the full ReAct reasoning loop.

    DESIGN DECISION — Dependency Injection:
        Both executor and planner are injected rather than hard-coded.
        This makes the agent fully testable: unit tests can pass mock
        executors that always succeed or always fail, without touching tools.

    Parameters
    ----------
    executor : Executor instance (injected for testability)
    planner  : Planner instance (injected for testability)
    """

    def __init__(
        self,
        executor: Optional[Executor] = None,
        planner: Optional[Planner] = None,
    ):
        # Use injected instances if provided, otherwise use shared singletons.
        # The Executor handles retry logic; the Planner handles all decisions.
        self._executor = executor or Executor()
        self._planner = planner or global_planner

    # ------------------------------------------------------------------
    async def process(self, ticket: Dict[str, Any]) -> AuditEntry:
        """
        Public entry point: run the full agent loop for one ticket.

        INTERVIEW TALKING POINT:
            This method is called concurrently for many tickets via
            asyncio.gather(). Each call is independent — no shared mutable
            state between tickets — making concurrency safe without locks.

        Returns the completed AuditEntry (used for logging and reporting).
        """
        ticket_id = ticket.get("ticket_id", "UNKNOWN")

        # Create a fresh audit entry for this ticket.
        # AuditEntry tracks every step taken for full explainability.
        entry = AuditEntry(ticket_id)

        console_log.info("=" * 70)
        console_log.info("🎫 Processing ticket: %s", ticket_id)
        console_log.info("   Subject : %s", ticket.get("subject", "N/A"))
        console_log.info("   Email   : %s", ticket.get("customer_email", "N/A"))
        console_log.info("=" * 70)

        try:
            # Run the core reasoning loop — this is where all the work happens.
            await self._run_react_loop(ticket, entry)
        except Exception as exc:
            # SAFETY NET: If anything unexpected crashes the loop,
            # we catch it here so the other concurrent tickets are unaffected.
            # The full stack trace is saved to the audit log for debugging.
            error_msg = f"Agent loop crashed: {exc}"
            console_log.error("💥 %s | ticket=%s", error_msg, ticket_id)
            entry.add_step(
                step_type="reflect",
                description=error_msg,
                success=False,
                error=traceback.format_exc(),  # full Python stack trace in audit log
            )
            entry.classification.setdefault("intent", "unknown")
            entry.finalize(
                final_action="escalated_due_to_system_error",
                confidence=0.0,
                status="failed",
                reason=f"Agent crashed unexpectedly: {type(exc).__name__} — {exc}",
            )

        # Always commit the audit log — even if the agent crashed.
        # This ensures no ticket is ever silently dropped.
        await audit_logger.commit(entry)
        return entry

    # ------------------------------------------------------------------
    async def _run_react_loop(
        self, ticket: Dict[str, Any], entry: AuditEntry
    ) -> None:
        """
        Core ReAct implementation: Observe → Classify → Plan → [Think → Act → Observe → Reflect] × N

        INTERVIEW TALKING POINT:
            This loop is inspired by the ReAct paper (Yao et al., 2022).
            Unlike a simple chatbot that generates one response, this loop:
            1. Maintains state across multiple tool calls (observations dict)
            2. Re-evaluates its confidence after each action
            3. Can change its plan mid-execution based on what tools return
            4. Has a safety cap (MAX_REASONING_STEPS) to prevent infinite loops
        """
        ticket_id = ticket.get("ticket_id", "UNKNOWN")

        # ── STEP 1: OBSERVE ──────────────────────────────────────────────
        # First step: understand what the ticket contains.
        # We sanitize sensitive fields (passwords, CVV) before logging.
        entry.add_step(
            step_type="observe",
            description="Ingested ticket and extracted key fields",
            tool_input=_sanitize_ticket(ticket),  # never log sensitive data
            success=True,
        )

        # ── STEP 2: CLASSIFY ─────────────────────────────────────────────
        # Ask the planner to determine WHAT the customer wants (intent),
        # HOW URGENTLY (urgency score 0-1), and HOW CONFIDENT we are (0-1).
        console_log.info("📋 Classifying ticket %s …", ticket_id)
        classification = self._planner.classifier.classify(ticket)

        # Store the classification result in the audit entry.
        # This is the "evidence" for every downstream decision.
        entry.classification = classification

        entry.add_step(
            step_type="observe",
            description=f"Classified as intent='{classification['intent']}' "
                        f"urgency={classification['urgency_score']:.2f} "
                        f"confidence={classification['confidence']:.2f}",
            tool_output=classification,
            success=True,
        )

        # ── STEP 3: PLAN ──────────────────────────────────────────────────
        # Build an ordered list of tool calls based on the detected intent.
        # e.g. refund_request → [get_order, get_customer, check_eligibility, issue_refund, send_reply]
        console_log.info("🧭 Building action plan …")
        plan = self._planner.build_plan(ticket, classification)

        entry.add_step(
            step_type="plan",
            description=f"Planned {len(plan)} tool actions: "
                        + ", ".join(a["tool_name"] for a in plan),
            tool_output={"plan": [{"tool": a["tool_name"], "reason": a["reason"]} for a in plan]},
            success=True,
        )

        console_log.info("  Action plan: %s", " → ".join(a["tool_name"] for a in plan))

        # ── STEP 4: ReAct LOOP ────────────────────────────────────────────
        # observations: stores what each tool returned, so later tools can
        # use that data (e.g. refund amount from check_refund_eligibility
        # is passed into issue_refund).
        observations: Dict[str, Any] = {}
        step_index = 0          # tracks which plan step we're on
        total_reasoning_steps = 0  # safety counter — caps the loop

        while total_reasoning_steps < agent_config.max_reasoning_steps:
            total_reasoning_steps += 1

            # Ask the planner: "What should I do next?"
            # The planner checks current confidence, observations, and plan position
            # to decide: continue / resolve / escalate.
            decision = self._planner.decide_next_action(
                plan, observations, classification, step_index
            )

            action = decision["action"]

            # ── Terminal: Resolve ─────────────────────────────────────────
            # All planned tools have been called and confidence is sufficient.
            if action == "resolve":
                await self._handle_resolve(ticket, classification, entry)
                return  # done — no more iterations needed

            # ── Terminal: Escalate ────────────────────────────────────────
            # Confidence too low, intent policy, or tool chain failed.
            if action == "escalate":
                await self._handle_escalate(ticket, classification, entry, decision["reason"])
                return  # done

            # ── Continue: Execute the next tool in the plan ───────────────
            next_step = decision["next_step"]
            tool_name = next_step["tool_name"]
            tool_args = next_step["args"]
            reason = next_step["reason"]

            # THINK: Log WHY we're calling this tool.
            # This is the "reasoning" part of ReAct — the agent explains itself.
            console_log.info(
                "🤔 [Step %d] THINK → Calling '%s': %s",
                total_reasoning_steps, tool_name, reason,
            )
            entry.add_step(
                step_type="plan",
                description=f"[Think] Will call '{tool_name}' — {reason}",
                tool_name=tool_name,
                success=True,
            )

            # ACT: Call the tool via the Executor.
            # The Executor handles retry logic — we don't need to here.
            try:
                result = await self._executor.run(
                    tool_name=tool_name,
                    tool_args=tool_args,
                    audit_entry=entry,   # executor records the act step
                )
                # Store result so the planner and later tools can reference it
                observations[tool_name] = result

            except ToolExecutionError as exc:
                # All retries exhausted — tool is genuinely unavailable.
                # Record the failure and degrade confidence; don't crash.
                console_log.warning("⚠️  Tool exhausted retries: %s", exc)
                observations[tool_name] = {
                    "success": False,
                    "error": str(exc),
                    "tool_name": tool_name,
                }
                entry.add_step(
                    step_type="reflect",
                    description=f"Tool '{tool_name}' failed after {exc.attempts} attempts — adjusting plan",
                    success=False,
                    error=exc.last_error,
                )
                # IMPORTANT: confidence penalty for tool failure.
                # If this drops confidence below 0.50, the planner will escalate.
                classification["confidence"] = max(
                    classification["confidence"] - 0.20, 0.10
                )

            # REFLECT: After every tool call, record the updated confidence.
            # The planner will read this in the next iteration to decide
            # whether to continue, resolve, or escalate.
            confidence = classification["confidence"]
            console_log.info(
                "💭 [Reflect] confidence=%.2f after '%s'", confidence, tool_name
            )
            entry.add_step(
                step_type="reflect",
                description=(
                    f"Observed result of '{tool_name}'; "
                    f"updated confidence={confidence:.2f}"
                ),
                tool_output={"confidence": confidence, "tool_name": tool_name},
                success=True,
            )

            step_index += 1  # advance to next planned tool

        # Safety cap: if we hit max steps, escalate rather than loop forever.
        console_log.warning("⛔ Max reasoning steps reached for ticket %s", ticket_id)
        await self._handle_escalate(
            ticket, classification, entry,
            reason=f"Exceeded maximum reasoning steps ({agent_config.max_reasoning_steps})",
        )

    # ------------------------------------------------------------------
    async def _handle_resolve(
        self,
        ticket: Dict[str, Any],
        classification: Dict[str, Any],
        entry: AuditEntry,
    ) -> None:
        """
        Finalize the ticket as resolved by sending a contextual reply.

        INTERVIEW TALKING POINT:
            The reply is composed using data collected during the reasoning
            loop — not a template. For example, if issue_refund returned a
            transaction ID, the reply includes that specific ID and amount.
            This is "retrieval-augmented generation" without an LLM.
        """
        ticket_id = ticket.get("ticket_id", "UNKNOWN")
        confidence = classification["confidence"]

        # Build a context-aware reply using data from the tool observations
        intent = classification["intent"]
        reply_message = _compose_resolution_reply(intent, ticket, entry)

        console_log.info("✅ Resolving ticket %s (confidence=%.2f)", ticket_id, confidence)

        # Send the reply via the send_reply tool (also retried if it fails)
        try:
            reply_result = await self._executor.run(
                tool_name="send_reply",
                tool_args={"ticket_id": ticket_id, "message": reply_message},
                audit_entry=entry,
            )
        except ToolExecutionError as exc:
            # Reply failed — record it but still mark the ticket resolved.
            # The underlying action (refund, etc.) was already taken.
            reply_result = {"success": False, "error": str(exc)}
            entry.add_step(
                step_type="reflect",
                description=f"Failed to send resolution reply: {exc}",
                success=False,
            )

        # Record the final resolution step and seal the audit entry
        entry.add_step(
            step_type="resolve",
            description=f"Ticket resolved with confidence={confidence:.2f}",
            tool_output={"reply_sent": reply_result.get("success", False)},
            success=True,
        )
        # Build a human-readable reason that summarises what happened during the loop
        reason = _build_resolve_reason(intent, classification, entry)
        entry.finalize(
            final_action=f"resolved_{intent}",
            confidence=confidence,
            status="resolved",
            reason=reason,
        )
        console_log.info("🎉 Ticket %s → RESOLVED | reason: %s", ticket_id, reason)

    # ------------------------------------------------------------------
    async def _handle_escalate(
        self,
        ticket: Dict[str, Any],
        classification: Dict[str, Any],
        entry: AuditEntry,
        reason: str = "Requires human review",
    ) -> None:
        """
        Escalate the ticket to a human agent with full context.

        INTERVIEW TALKING POINT:
            Escalation is not a failure — it's a feature.
            The escalation message contains the intent, urgency score,
            current confidence, and the specific reason why the agent
            could not resolve it. A human agent receives rich context,
            not just "please review this ticket."
        """
        ticket_id = ticket.get("ticket_id", "UNKNOWN")
        confidence = classification["confidence"]
        priority = classification.get("priority", "medium")

        console_log.info(
            "🚨 Escalating ticket %s | reason: %s | confidence=%.2f",
            ticket_id, reason, confidence,
        )

        # Build a summary that gives the human agent maximum context.
        # They see: what the customer wanted, how urgent, why we couldn't resolve.
        escalation_summary = (
            f"Intent: {classification.get('intent', 'unknown')} | "
            f"Urgency: {classification.get('urgency_score', 0):.2f} | "
            f"Confidence: {confidence:.2f} | "
            f"Reason: {reason}"
        )

        try:
            esc_result = await self._executor.run(
                tool_name="escalate",
                tool_args={
                    "ticket_id": ticket_id,
                    "summary": escalation_summary,
                    "priority": priority,   # maps to human queue: support-low/medium/high/critical
                },
                audit_entry=entry,
            )
        except ToolExecutionError as exc:
            # Even the escalate tool failed — record it and mark the entry
            esc_result = {"success": False, "error": str(exc)}
            entry.add_step(
                step_type="reflect",
                description=f"Escalation tool also failed: {exc}",
                success=False,
            )

        # Record the escalation step and seal the audit entry
        # Build a human-readable escalation reason for the audit record
        escalation_reason = _build_escalate_reason(reason, classification, entry)
        entry.add_step(
            step_type="escalate",
            description=f"Escalated: {reason}",
            tool_output=esc_result,
            success=esc_result.get("success", False),
        )
        entry.finalize(
            final_action=f"escalated_{reason[:60]}",
            confidence=confidence,
            status="escalated",
            reason=escalation_reason,
        )
        console_log.info("📤 Ticket %s → ESCALATED | reason: %s", ticket_id, escalation_reason)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _sanitize_ticket(ticket: Dict[str, Any]) -> Dict[str, Any]:
    """
    Remove sensitive fields before logging ticket data.

    INTERVIEW TALKING POINT:
        In production, audit logs may be stored in S3 or sent to monitoring
        services. We must never log raw sensitive data. This function
        redacts known PII/payment fields before they hit the audit trail.
    """
    sensitive = {"password", "ssn", "credit_card", "cvv", "pin"}
    return {k: ("***REDACTED***" if k in sensitive else v) for k, v in ticket.items()}


def _compose_resolution_reply(
    intent: str,
    ticket: Dict[str, Any],
    entry: AuditEntry,
) -> str:
    """
    Build a personalised resolution reply using data from tool observations.

    INTERVIEW TALKING POINT:
        Instead of a static template, this function extracts actual data
        from the tool outputs recorded in the AuditEntry steps. For a
        refund request, it finds the transaction ID issued by issue_refund
        and includes it in the reply. This is grounded generation.
    """
    name = ticket.get("customer_name", "Valued Customer")

    # Extract tool outputs recorded during the reasoning loop from the audit steps.
    # Only successful "act" steps with output are included.
    tool_outputs = {
        step.get("tool_name"): step.get("tool_output")
        for step in entry.steps
        if step.get("type") == "act" and step.get("success") and step.get("tool_output")
    }

    # Compose intent-specific replies using real data from tool calls
    if intent == "refund_request":
        refund_info = tool_outputs.get("issue_refund") or {}
        if refund_info.get("transaction_id"):
            # Refund was issued — include the transaction ID and amount
            return (
                f"Dear {name},\n\n"
                f"Your refund of ${refund_info['refund_amount']:.2f} has been approved and processed. "
                f"Transaction ID: {refund_info['transaction_id']}. "
                f"Please allow {refund_info.get('processing_days', '5-7')} business days for the amount to reflect.\n\n"
                f"Thank you for your patience.\n\nSupport Team"
            )
        # Refund couldn't be issued — explain why using eligibility check result
        elig = tool_outputs.get("check_refund_eligibility") or {}
        return (
            f"Dear {name},\n\n"
            f"We reviewed your refund request. {elig.get('reason', 'Your request has been reviewed.')} "
            f"If you believe this is an error, please reply with your order details.\n\n"
            f"Support Team"
        )

    if intent == "order_status":
        order = tool_outputs.get("get_order") or {}
        return (
            f"Dear {name},\n\n"
            f"Your order {order.get('order_id', '')} is currently '{order.get('status', 'being processed')}'. "
            f"If the status shows 'in_transit', you can track it via our carrier portal. "
            f"Expected delivery within 3-5 business days.\n\n"
            f"Support Team"
        )

    # Generic reply for other intents — use knowledge base content if available
    kb_info = tool_outputs.get("search_knowledge_base") or {}
    articles = kb_info.get("results", [])
    article_text = ""
    if articles:
        # Prepend the most relevant KB article content to the reply
        article_text = " " + articles[0].get("content", "")

    return (
        f"Dear {name},\n\n"
        f"Thank you for reaching out.{article_text} "
        f"If you need further assistance, please don't hesitate to reply to this message.\n\n"
        f"Support Team"
    )


def _build_resolve_reason(
    intent: str,
    classification: Dict[str, Any],
    entry: AuditEntry,
) -> str:
    """
    Build a plain-English sentence explaining WHY the ticket was resolved.
    Uses data collected from tool outputs during the reasoning loop.
    """
    conf = classification.get("confidence", 0)

    # Gather successful tool outputs from the audit steps
    tool_outputs = {
        step.get("tool_name"): step.get("tool_output")
        for step in entry.steps
        if step.get("type") == "act" and step.get("success") and step.get("tool_output")
    }

    if intent == "refund_request":
        refund = tool_outputs.get("issue_refund") or {}
        elig   = tool_outputs.get("check_refund_eligibility") or {}
        if refund.get("transaction_id"):
            return (
                f"Refund of ${refund['refund_amount']:.2f} approved and processed "
                f"(Transaction {refund['transaction_id']}). "
                f"Eligibility reason: {elig.get('reason', 'met return policy')}. "
                f"Confidence: {conf:.0%}."
            )
        if elig:
            return (
                f"Refund request reviewed. {elig.get('reason', 'Eligibility could not be confirmed')}. "
                f"Confidence: {conf:.0%}."
            )
        return f"Refund request processed with confidence {conf:.0%}."

    if intent == "order_status":
        order = tool_outputs.get("get_order") or {}
        status = order.get("status", "unknown")
        return (
            f"Order status '{status}' retrieved and communicated to customer. "
            f"Confidence: {conf:.0%}."
        )

    if intent == "product_inquiry":
        product = tool_outputs.get("get_product") or {}
        name = product.get("name", "requested product")
        return (
            f"Product information for '{name}' retrieved from catalogue and sent to customer. "
            f"Confidence: {conf:.0%}."
        )

    if intent == "technical_support":
        kb = tool_outputs.get("search_knowledge_base") or {}
        count = kb.get("total_found", 0)
        return (
            f"Technical support resolved using {count} knowledge-base article(s). "
            f"Confidence: {conf:.0%}."
        )

    if intent == "account_issue":
        kb = tool_outputs.get("search_knowledge_base") or {}
        count = kb.get("total_found", 0)
        return (
            f"Account issue addressed using {count} knowledge-base article(s). "
            f"Reply sent to customer. Confidence: {conf:.0%}."
        )

    # Generic fallback
    tools_run = ", ".join(entry.tools_used) or "N/A"
    return (
        f"Ticket resolved after executing tools [{tools_run}] "
        f"with confidence {conf:.0%}."
    )


def _build_escalate_reason(
    trigger: str,
    classification: Dict[str, Any],
    entry: AuditEntry,
) -> str:
    """
    Build a plain-English sentence explaining WHY the ticket was escalated.
    Combines the immediate trigger with context from the classification.
    """
    intent    = classification.get("intent", "unknown")
    conf      = classification.get("confidence", 0)
    urgency   = classification.get("urgency_score", 0)
    priority  = classification.get("priority", "medium")

    # Count how many tool failures occurred during the loop
    failed_tools = [
        step.get("tool_name", "unknown")
        for step in entry.steps
        if step.get("type") == "act" and not step.get("success")
    ]

    parts = [trigger]

    if "Low confidence" in trigger:
        parts = [
            f"Escalated due to low agent confidence ({conf:.0%}) "
            f"after processing intent '{intent}'"
        ]
        if failed_tools:
            unique_failed = list(dict.fromkeys(failed_tools))  # deduplicate, preserve order
            parts.append(
                f" — tool failures degraded confidence: [{', '.join(unique_failed)}]"
            )
        parts.append(f". Urgency: {urgency:.0%}. Priority queue: {priority}.")

    elif "always routed" in trigger or "policy" in trigger.lower():
        parts = [
            f"Escalated by routing policy: intent '{intent}' requires human judgment. "
            f"Confidence was {conf:.0%} and urgency {urgency:.0%}. "
            f"Routed to {priority}-priority queue."
        ]

    elif "Max" in trigger or "steps" in trigger.lower():
        parts = [
            f"Escalated after exhausting the maximum reasoning steps. "
            f"Intent '{intent}', confidence {conf:.0%}, urgency {urgency:.0%}."
        ]

    elif "system_error" in trigger.lower() or "crashed" in trigger.lower():
        parts = [f"Escalated due to unexpected system error during processing. {trigger}"]

    return "".join(parts)
