import os
import sys
import json
import time
import asyncio
import pandas as pd
import streamlit as st

# Add agent_system directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "agent_system"))

from agent_system.agent import SupportAgent
from agent_system.config import agent_config
from agent_system.logger import audit_logger


# ==============================================================================
# UI CONFIGURATION
# ==============================================================================
st.set_page_config(
    page_title="Autonomous Support Agent",
    page_icon="🤖",
    layout="wide",
)

st.title("🤖 Autonomous Support Resolution Agent")
st.markdown("""
Welcome to the Autonomous Support Agent Dashboard. This system uses a **ReAct-style** reasoning loop 
to autonomously classify, process, and resolve or escalate customer support tickets.
""")


# ==============================================================================
# ASYNC PROCESSING LOOP
# ==============================================================================
async def process_tickets_concurrently(tickets, progress_bar, status_text):
    """Run all tickets concurrently and update progress."""
    agent = SupportAgent()
    semaphore = asyncio.Semaphore(agent_config.max_concurrent_tickets)
    
    # Optional context for live updates
    completed = 0
    total = len(tickets)
    results = []

    async def process_one(ticket):
        nonlocal completed
        async with semaphore:
            # Process ticket using core logic
            entry = await agent.process(ticket)
            
            # Update Progress (UI)
            completed += 1
            progress_bar.progress(completed / total)
            status_text.text(f"Processed {completed} of {total} tickets...")
            
            return entry.to_dict()

    # Create async tasks for all tickets
    tasks = [asyncio.create_task(process_one(t)) for t in tickets]
    
    # Gather results
    results = await asyncio.gather(*tasks)
    return results


# ==============================================================================
# SIDEBAR / CONTROLS
# ==============================================================================
with st.sidebar:
    st.header("⚙️ Configuration")
    failure_rate = st.slider(
        "Simulated Tool Failure Rate", 
        min_value=0.0, max_value=1.0, value=agent_config.tool_failure_rate, step=0.05,
        help="Simulates network/tool failures. The agent will attempt to retry and recover."
    )
    max_concurrent = st.slider(
        "Max Concurrent Tickets",
        min_value=1, max_value=50, value=agent_config.max_concurrent_tickets, step=1
    )
    
    # Apply to singleton config
    agent_config.tool_failure_rate = failure_rate
    agent_config.max_concurrent_tickets = max_concurrent


# ==============================================================================
# MAIN UPLOAD & EXECUTION
# ==============================================================================
st.subheader("1. Upload Tickets")
uploaded_file = st.file_uploader("Upload a tickets JSON file (e.g., tickets.json)", type=["json"])

if uploaded_file:
    try:
        tickets = json.load(uploaded_file)
        if not isinstance(tickets, list):
            st.error("Invalid JSON format: Expected a list of tickets.")
            st.stop()
    except json.JSONDecodeError:
        st.error("Failed to parse JSON file.")
        st.stop()
        
    st.success(f"Successfully loaded {len(tickets)} tickets.")
    
    # Display raw input preview
    with st.expander("Preview input data"):
        st.json(tickets[:2]) # show max 2 as preview
        
    st.divider()
    st.subheader("2. Execute Agent")
    
    if st.button("🚀 Run Autonomous Agent", use_container_width=True):
        
        # UI Elements for progress
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        start_time = time.time()
        
        # Run event loop to wrap asyncio code
        with st.spinner("Agent is reasoning..."):
            results = asyncio.run(process_tickets_concurrently(tickets, progress_bar, status_text))
            
        elapsed = time.time() - start_time
        status_text.success(f"Execution completed in {elapsed:.2f} seconds!")
        
        # Ensure we always scope the summary to this exact run
        # Wait, the audit_logger accumulates records natively, but we can compute stats from `results`
        
        # ==============================================================================
        # METRICS & SUMMARY
        # ==============================================================================
        st.divider()
        st.subheader("3. Final Summary & Results")
        
        total_processed = len(results)
        resolved_count = sum(1 for r in results if r.get("status") == "resolved")
        escalated_count = sum(1 for r in results if r.get("status") == "escalated")
        failed_count = sum(1 for r in results if r.get("status") == "failed")
        avg_confidence = (sum(r.get("confidence", 0) for r in results) / total_processed) if total_processed else 0
        
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Tickets", total_processed)
        col2.metric("✅ Resolved", resolved_count)
        col3.metric("📤 Escalated", escalated_count)
        col4.metric("🎯 Avg Confidence", f"{avg_confidence:.0%}")
        
        if failed_count > 0:
            st.error(f"{failed_count} tickets encountered internal system errors/crashes.")

        # ==============================================================================
        # DATAFRAME VIEW
        # ==============================================================================
        st.markdown("### Per-Ticket Breakdown")
        
        df_data = []
        for r in results:
            cls = r.get("classification", {})
            df_data.append({
                "Ticket ID": r.get("ticket_id"),
                "Intent": cls.get("intent", "Unknown"),
                "Status": r.get("status", "Unknown").upper(),
                "Confidence": f"{r.get('confidence', 0):.0%}",
                "Final Action": r.get("final_action", ""),
                "Reason": r.get("reason", "")
            })
            
        df = pd.DataFrame(df_data)
        
        # Styling the dataframe for colors based on status
        def style_status(val):
            color = 'green' if val == 'RESOLVED' else 'orange' if val == 'ESCALATED' else 'red'
            return f'color: {color}; font-weight: bold'
            
        styled_df = df.style.map(style_status, subset=['Status'])
        st.dataframe(styled_df, use_container_width=True, hide_index=True)
        
        # ==============================================================================
        # DETAILED LOG EXPANDERS
        # ==============================================================================
        st.markdown("### Detailed Reasoning Logs")
        for r in results:
            status_icon = "✅" if r.get("status") == "resolved" else "📤" if r.get("status") == "escalated" else "❌"
            expander_title = f"{status_icon} Ticket {r.get('ticket_id')} — {r.get('classification', {}).get('intent', 'unknown')} ({r.get('confidence', 0):.0%} conf)"
            
            with st.expander(expander_title):
                st.markdown(f"**Final Reason:** {r.get('reason')}")
                st.markdown(f"**Tools Used:** `{', '.join(r.get('tools_used', []))}`")
                
                # Show steps safely in a secondary expander or just write them nicely as json
                st.json(r)
