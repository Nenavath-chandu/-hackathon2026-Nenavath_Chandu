"""
logger.py - Structured audit logging system for the Support Resolution Agent.

Every reasoning step, tool call, decision, and outcome is captured in a
structured JSON audit log. Logs are thread/async-safe via asyncio.Lock.
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config import agent_config

# ---------------------------------------------------------------------------
# Console logger (human-readable, colour-coded via level)
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
console_log = logging.getLogger("agent_system")


# ---------------------------------------------------------------------------
# AuditEntry — typed structure matching the spec
# ---------------------------------------------------------------------------
class AuditEntry:
    """Represents a single ticket's complete audit record."""

    def __init__(self, ticket_id: str):
        self.ticket_id = ticket_id
        self.classification: Dict[str, Any] = {}
        self.steps: List[Dict[str, Any]] = []
        self.tools_used: List[str] = []
        self.final_action: str = ""
        self.reason: str = ""          # WHY the final decision was made (human-readable)
        self.confidence: float = 0.0
        self.status: str = "pending"
        self.started_at: str = datetime.now(timezone.utc).isoformat()
        self.completed_at: Optional[str] = None
        self.total_duration_ms: Optional[float] = None
        self._start_ts: float = time.monotonic()

    # ------------------------------------------------------------------
    def add_step(
        self,
        step_type: str,
        description: str,
        tool_name: Optional[str] = None,
        tool_input: Optional[Dict] = None,
        tool_output: Optional[Any] = None,
        success: bool = True,
        error: Optional[str] = None,
        duration_ms: Optional[float] = None,
    ) -> None:
        step = {
            "step_number": len(self.steps) + 1,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": step_type,          # observe | plan | act | reflect | escalate | resolve
            "description": description,
            "success": success,
        }
        if tool_name:
            step["tool_name"] = tool_name
            if tool_name not in self.tools_used:
                self.tools_used.append(tool_name)
        if tool_input is not None:
            step["tool_input"] = tool_input
        if tool_output is not None:
            step["tool_output"] = tool_output
        if error:
            step["error"] = error
        if duration_ms is not None:
            step["duration_ms"] = round(duration_ms, 2)
        self.steps.append(step)

    # ------------------------------------------------------------------
    def finalize(
        self,
        final_action: str,
        confidence: float,
        status: str,
        reason: str = "",
    ) -> None:
        self.final_action = final_action
        self.reason = reason            # human-readable explanation of the decision
        self.confidence = round(confidence, 4)
        self.status = status
        self.completed_at = datetime.now(timezone.utc).isoformat()
        self.total_duration_ms = round(
            (time.monotonic() - self._start_ts) * 1000, 2
        )

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticket_id": self.ticket_id,
            "classification": self.classification,
            "steps": self.steps,
            "tools_used": self.tools_used,
            "final_action": self.final_action,
            "reason": self.reason,      # top-level WHY field
            "confidence": self.confidence,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "total_duration_ms": self.total_duration_ms,
        }


# ---------------------------------------------------------------------------
# AuditLogger — async-safe JSON sink
# ---------------------------------------------------------------------------
class AuditLogger:
    """
    Collects AuditEntry objects and persists them to logs/audit_log.json.
    Uses an asyncio.Lock so concurrent coroutines never corrupt the file.
    """

    def __init__(self, log_file: str = agent_config.audit_log_file):
        self._log_file = log_file
        self._lock = asyncio.Lock()
        self._records: List[Dict[str, Any]] = []
        os.makedirs(os.path.dirname(log_file), exist_ok=True)

        # Load existing records if the file already exists
        if os.path.exists(log_file):
            try:
                with open(log_file, "r", encoding="utf-8") as fh:
                    existing = json.load(fh)
                    if isinstance(existing, list):
                        self._records = existing
            except (json.JSONDecodeError, OSError):
                self._records = []

    # ------------------------------------------------------------------
    async def commit(self, entry: AuditEntry) -> None:
        """Append a finalised AuditEntry and flush to disk."""
        async with self._lock:
            record = entry.to_dict()
            self._records.append(record)
            await self._flush()
            console_log.info(
                "[AUDIT] ticket=%s status=%s confidence=%.2f tools=%s",
                entry.ticket_id,
                entry.status,
                entry.confidence,
                entry.tools_used,
            )

    # ------------------------------------------------------------------
    async def _flush(self) -> None:
        """
        Write all records to disk atomically.

        Windows-safe strategy:
          1. Write to a .tmp file (file handle explicitly closed first)
          2. If the original exists, delete it (avoids PermissionError on rename)
          3. Rename .tmp → original
          4. Retry up to 3 times on PermissionError with 100ms back-off
        """
        tmp_path = self._log_file + ".tmp"
        max_attempts = 3

        for attempt in range(1, max_attempts + 1):
            try:
                # Step 1: write to temp — close handle before any file-system ops
                with open(tmp_path, "w", encoding="utf-8") as fh:
                    json.dump(self._records, fh, indent=2, ensure_ascii=False)

                # Step 2: remove original if it exists (Windows can't rename over open file)
                if os.path.exists(self._log_file):
                    os.remove(self._log_file)

                # Step 3: rename temp → final
                os.rename(tmp_path, self._log_file)
                return  # success — done

            except PermissionError as exc:
                console_log.warning(
                    "[AUDIT] Flush attempt %d/%d failed (PermissionError): %s",
                    attempt, max_attempts, exc,
                )
                if attempt < max_attempts:
                    await asyncio.sleep(0.1 * attempt)  # 100ms, 200ms back-off
                else:
                    console_log.error(
                        "[AUDIT] All flush attempts failed — audit data kept in memory."
                    )
                    # Clean up orphaned tmp file if present
                    try:
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)
                    except OSError:
                        pass

            except OSError as exc:
                console_log.error("[AUDIT] Failed to write audit log: %s", exc)
                return  # non-retryable OS error

    # ------------------------------------------------------------------
    def get_summary(self, current_run_count: Optional[int] = None) -> Dict[str, Any]:
        """
        Return aggregate statistics.

        Parameters
        ----------
        current_run_count : int, optional
            If provided, stats are computed over only the last N records
            (i.e., the tickets processed in the *current* run), ignoring
            records accumulated from previous runs in the same log file.
            If None, all records in memory are included.
        """
        if current_run_count is not None:
            records = self._records[-current_run_count:] if current_run_count > 0 else []
        else:
            records = self._records

        total = len(records)
        resolved  = sum(1 for r in records if r.get("status") == "resolved")
        escalated = sum(1 for r in records if r.get("status") == "escalated")
        failed    = sum(1 for r in records if r.get("status") == "failed")
        avg_conf  = (
            sum(r.get("confidence", 0) for r in records) / total
            if total else 0.0
        )
        avg_steps = (
            sum(len(r.get("steps", [])) for r in records) / total
            if total else 0.0
        )
        return {
            "total_tickets": total,
            "resolved": resolved,
            "escalated": escalated,
            "failed": failed,
            "avg_confidence": round(avg_conf, 4),
            "avg_steps_per_ticket": round(avg_steps, 2),
        }


# Singleton shared across the system
audit_logger = AuditLogger()
