"""
main.py - Entry point for the Autonomous Support Resolution Agent.

Usage
-----
    python main.py                    # processes data/tickets.json
    python main.py --tickets path.json
    python main.py --help

Features
--------
  • Loads tickets from JSON (validated)
  • Processes all tickets concurrently via asyncio
  • Semaphore controls max parallelism (config.MAX_CONCURRENT_TICKETS)
  • Prints a rich summary report on completion
  • Writes full audit log to logs/audit_log.json
"""

import argparse
import asyncio
import json
import os
import sys
import time

# Ensure agent_system package is on the path when run from any cwd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent import SupportAgent
from config import agent_config
from logger import audit_logger, console_log
from utils.helpers import (
    generate_summary_report,
    load_tickets,
    print_banner,
)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Autonomous Support Resolution Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py
  python main.py --tickets data/custom_tickets.json
  python main.py --max-concurrent 5
        """,
    )
    parser.add_argument(
        "--tickets",
        default=agent_config.tickets_file,
        help="Path to tickets JSON file (default: data/tickets.json)",
    )
    parser.add_argument(
        "--max-concurrent",
        type=int,
        default=agent_config.max_concurrent_tickets,
        help=f"Max concurrent tickets (default: {agent_config.max_concurrent_tickets})",
    )
    parser.add_argument(
        "--failure-rate",
        type=float,
        default=agent_config.tool_failure_rate,
        help=f"Simulated tool failure rate 0-1 (default: {agent_config.tool_failure_rate})",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Core async processing
# ---------------------------------------------------------------------------

async def process_ticket_with_semaphore(
    semaphore: asyncio.Semaphore,
    agent: SupportAgent,
    ticket: dict,
) -> dict:
    """Acquire semaphore slot, process ticket, release."""
    async with semaphore:
        console_log.info("▶ Processing ticket %s", ticket.get('ticket_id'))
        entry = await agent.process(ticket)
        return entry.to_dict()


async def run_agent_system(tickets_file: str, max_concurrent: int) -> None:
    """
    Main async coroutine:
      1. Load tickets
      2. Spin up agent coroutines (all concurrent)
      3. Gather results
      4. Print summary
    """
    print_banner("AUTONOMOUS SUPPORT RESOLUTION AGENT v1.0")

    # Load tickets
    try:
        tickets = load_tickets(tickets_file)
    except (FileNotFoundError, ValueError) as exc:
        console_log.error("Failed to load tickets: %s", exc)
        sys.exit(1)

    if not tickets:
        console_log.error("No valid tickets found in %s", tickets_file)
        sys.exit(1)

    console_log.info(
        "🚀 Starting concurrent processing: %d tickets | max_concurrent=%d",
        len(tickets),
        max_concurrent,
    )

    semaphore = asyncio.Semaphore(max_concurrent)
    agent = SupportAgent()

    t0 = time.monotonic()

    # Launch all tickets concurrently
    tasks = [
        asyncio.create_task(
            process_ticket_with_semaphore(semaphore, agent, ticket),
            name=f"ticket-{ticket.get('ticket_id', i)}",
        )
        for i, ticket in enumerate(tickets)
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    elapsed = time.monotonic() - t0

    # Handle any task-level exceptions (shouldn't happen — agent catches internally)
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            console_log.error("Task %d raised unhandled exception: %s", i, result)

    # Print final summary — scope to THIS run only (last N records)
    num_tickets = len(tickets)
    summary = audit_logger.get_summary(current_run_count=num_tickets)
    audit_records = audit_logger._records[-num_tickets:]

    report = generate_summary_report(summary, audit_records)
    print(report)

    console_log.info(
        "⏱  Total wall-clock time: %.2fs for %d tickets (%.2f tickets/s)",
        elapsed,
        len(tickets),
        len(tickets) / elapsed if elapsed > 0 else 0,
    )
    console_log.info("📁 Audit log saved to: %s", agent_config.audit_log_file)

    # Print audit log location as JSON pointer
    print(f"\n  📄 Full audit log → {agent_config.audit_log_file}\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()

    # Apply CLI overrides to config
    agent_config.tool_failure_rate = args.failure_rate
    agent_config.max_concurrent_tickets = args.max_concurrent

    console_log.info("Agent config | failure_rate=%.0f%% | max_concurrent=%d",
                     agent_config.tool_failure_rate * 100,
                     agent_config.max_concurrent_tickets)

    asyncio.run(run_agent_system(args.tickets, args.max_concurrent))


if __name__ == "__main__":
    main()
