"""
executor.py - Executes tool calls with retry logic and exponential back-off.

INTERVIEW TALKING POINT:
    The Executor is the "reliability layer" of the agent. It sits between
    the planning layer (which decides WHAT to call) and the tools themselves
    (which do the actual work).

    Isolating retry logic here means:
      • agent.py never needs to think about retries — it just calls executor.run()
      • Tools are simple async functions — no retry logic inside them
      • The retry strategy can be swapped (e.g. circuit breaker) without
        touching agent.py or tools.py

    This follows the Single Responsibility Principle:
      • agent.py   → orchestration
      • planner.py → decisions
      • executor.py → reliable execution
      • tools.py   → business logic

Responsibilities
----------------
  • Retry with exponential back-off (max 2 retries per call)
  • Structured result / error reporting
  • Per-call duration tracking
  • Audit step recording (every attempt, success or failure)
"""

import asyncio
import time
from typing import Any, Dict, Optional

from config import agent_config
from logger import AuditEntry, console_log
from tools import TOOL_REGISTRY


class ToolExecutionError(Exception):
    """
    Raised when a tool fails after ALL retry attempts are exhausted.

    INTERVIEW TALKING POINT:
        Using a custom exception (rather than a generic RuntimeError) lets
        agent.py catch ONLY tool failures specifically, while still allowing
        unexpected exceptions (AttributeError, KeyError, etc.) to bubble up
        to the outer try/except in process() for crash-safe handling.

    Attributes
    ----------
    tool_name  : which tool failed
    attempts   : how many times it was attempted
    last_error : the error message from the final attempt
    """

    def __init__(self, tool_name: str, attempts: int, last_error: str):
        self.tool_name = tool_name
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            f"Tool '{tool_name}' failed after {attempts} attempt(s): {last_error}"
        )


class Executor:
    """
    Executes a single named tool call with automatic retry + back-off.

    INTERVIEW TALKING POINT:
        Exponential backoff is important for distributed systems. If a service
        is temporarily overloaded, hammering it immediately can make things worse.
        Waiting progressively longer (0.5s, 1.0s) gives the service time to recover.

        Formula: delay = base_delay × (backoff_factor ^ attempt_number)
          Attempt 0 fails → wait 0.5 × 2⁰ = 0.5s
          Attempt 1 fails → wait 0.5 × 2¹ = 1.0s
          Attempt 2 fails → raise ToolExecutionError

    Usage
    -----
    executor = Executor()
    result = await executor.run(
        tool_name="get_order",
        tool_args={"order_id": "ORD-1001"},
        audit_entry=entry,
    )
    """

    def __init__(
        self,
        max_retries: int = agent_config.max_retries,
        base_delay: float = agent_config.retry_base_delay,
        backoff_factor: float = agent_config.retry_backoff_factor,
    ):
        # All retry parameters are configurable — no magic numbers in the code.
        # In production, these would come from environment variables or a config service.
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.backoff_factor = backoff_factor

    # ------------------------------------------------------------------
    async def run(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        audit_entry: Optional[AuditEntry] = None,
    ) -> Dict[str, Any]:
        """
        Execute a tool by name, retrying on failure with exponential back-off.

        INTERVIEW TALKING POINT:
            The audit_entry parameter is optional (None-safe). This means
            the Executor can be used in unit tests without any audit system,
            and the audit step recording is a side-effect — not required for
            the core execution logic to work.

        Parameters
        ----------
        tool_name   : Name of the tool (must be in TOOL_REGISTRY)
        tool_args   : Keyword arguments forwarded to the tool async function
        audit_entry : If provided, each attempt is recorded as a step in the log

        Returns
        -------
        dict   — structured tool output on success

        Raises
        ------
        ToolExecutionError   — if all retries are exhausted
        ValueError           — if tool_name is not found in the registry
        """
        # Validate the tool name up-front — fail fast rather than silently
        if tool_name not in TOOL_REGISTRY:
            raise ValueError(f"Unknown tool: '{tool_name}'. Available: {list(TOOL_REGISTRY)}")

        # Look up the async function for this tool
        tool_fn = TOOL_REGISTRY[tool_name]
        last_error: str = ""
        attempts = 0

        # Loop: attempt 0, 1, 2 (initial + max_retries retries)
        for attempt in range(self.max_retries + 1):
            attempts += 1
            t0 = time.monotonic()  # wall-clock start for duration tracking

            try:
                console_log.info(
                    "  → Calling tool '%s' (attempt %d/%d) | args=%s",
                    tool_name,
                    attempt + 1,
                    self.max_retries + 1,
                    _truncate_repr(tool_args),  # truncate long args for readable logs
                )

                # ACTUAL TOOL CALL — await the async tool function
                result: Dict[str, Any] = await tool_fn(**tool_args)
                duration_ms = (time.monotonic() - t0) * 1000

                console_log.info(
                    "  ✓ Tool '%s' succeeded in %.0fms | success=%s",
                    tool_name,
                    duration_ms,
                    result.get("success", "?"),
                )

                # Record the successful execution in the audit log.
                # We truncate large outputs to keep the log file readable.
                if audit_entry:
                    audit_entry.add_step(
                        step_type="act",
                        description=f"Executed tool '{tool_name}'",
                        tool_name=tool_name,
                        tool_input=tool_args,
                        tool_output=_safe_truncate(result),
                        success=True,
                        duration_ms=duration_ms,
                    )

                return result  # success — no more retries needed

            except Exception as exc:
                # Tool raised an exception — record the failure and decide whether to retry
                duration_ms = (time.monotonic() - t0) * 1000
                last_error = str(exc)

                console_log.warning(
                    "  ✗ Tool '%s' failed (attempt %d) after %.0fms: %s",
                    tool_name,
                    attempt + 1,
                    duration_ms,
                    last_error,
                )

                # Record the failed attempt — gives full retry history in the audit log
                if audit_entry:
                    audit_entry.add_step(
                        step_type="act",
                        description=f"Tool '{tool_name}' attempt {attempt + 1} failed",
                        tool_name=tool_name,
                        tool_input=tool_args,
                        success=False,
                        error=last_error,
                        duration_ms=duration_ms,
                    )

                if attempt < self.max_retries:
                    # Calculate back-off delay before next retry
                    # Formula: base_delay × backoff_factor^attempt
                    # e.g.: 0.5 × 2⁰ = 0.5s, then 0.5 × 2¹ = 1.0s
                    delay = self.base_delay * (self.backoff_factor ** attempt)
                    console_log.info(
                        "  ↺ Retrying '%s' in %.2fs …", tool_name, delay
                    )
                    await asyncio.sleep(delay)  # non-blocking wait — other tickets run during this
                # else: no sleep after final attempt — fall through to raise

        # All attempts exhausted — propagate as a typed exception
        # so agent.py can catch THIS specifically and degrade gracefully
        raise ToolExecutionError(
            tool_name=tool_name,
            attempts=attempts,
            last_error=last_error,
        )


# ---------------------------------------------------------------------------
# Private helper functions
# ---------------------------------------------------------------------------

def _truncate_repr(obj: Any, max_len: int = 120) -> str:
    """
    Truncate a repr() string for readable log output.
    Prevents huge dict/list arguments from flooding the console.
    """
    raw = repr(obj)
    return raw if len(raw) <= max_len else raw[:max_len] + "…"


def _safe_truncate(data: Dict[str, Any], max_str_len: int = 200) -> Dict[str, Any]:
    """
    Truncate long string values and lists in a dict for audit log readability.

    INTERVIEW TALKING POINT:
        Audit logs grow quickly. Without truncation, a single knowledge-base
        result with long article text could make each log entry 10KB+.
        We truncate at 200 chars and keep only the first 5 list items,
        which preserves enough information for debugging without bloating the log.
    """
    result = {}
    for k, v in data.items():
        if isinstance(v, str) and len(v) > max_str_len:
            result[k] = v[:max_str_len] + "…"   # mark truncation with ellipsis
        elif isinstance(v, list):
            result[k] = v[:5]  # never store more than 5 list items in the audit log
        else:
            result[k] = v
    return result
