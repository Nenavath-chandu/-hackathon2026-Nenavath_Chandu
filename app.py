import os
import sys
import json
import time
import tempfile
import subprocess
import pandas as pd
import streamlit as st

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
# SIDEBAR / CONTROLS
# ==============================================================================
with st.sidebar:
    st.header("⚙️ Configuration")
    failure_rate = st.slider(
        "Simulated Tool Failure Rate", 
        min_value=0.0, max_value=1.0, value=0.20, step=0.05,
        help="Simulates network/tool failures. The agent will attempt to retry and recover."
    )
    max_concurrent = st.slider(
        "Max Concurrent Tickets",
        min_value=1, max_value=50, value=10, step=1
    )


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
        
        start_time = time.time()
        
        # 1. Save uploaded file to a temporary file so subprocess can read it
        with tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode='w', encoding='utf-8') as tmp_file:
            json.dump(tickets, tmp_file)
            tmp_file_path = tmp_file.name

        # Prepare CLI command
        cmd = [
            sys.executable, "agent_system/main.py",
            "--tickets", tmp_file_path,
            "--max-concurrent", str(max_concurrent),
            "--failure-rate", str(failure_rate)
        ]

        # 3. Setup Environment to force UTF-8 for Windows compatibility
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"

        # 4. Run agent system in subprocess and capture output
        with st.spinner("🧠 Agent is reasoning... Please wait while it processes tickets."):
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env)
            
        elapsed = time.time() - start_time

        # Clean up temp file
        try:
            os.remove(tmp_file_path)
        except OSError:
            pass

        # Try to read generated audit log to populate UI
        audit_file_path = "agent_system/logs/audit_log.json"
        if os.path.exists(audit_file_path):
            with open(audit_file_path, "r", encoding="utf-8") as file:
                full_audit_log = json.load(file)
                results = full_audit_log[-len(tickets):] if len(full_audit_log) >= len(tickets) else full_audit_log
        else:
            results = []

        # ==============================================================================
        # DASHBOARD UI - IMPRESSIVE LAYOUT
        # ==============================================================================
        st.divider()
        st.success(f"✨ Execution completed successfully in {elapsed:.2f} seconds!")
        
        # Create Tabs for a cleaner user experience
        tab_summary, tab_logs, tab_deep_dive = st.tabs([
            "📊 Executive Summary", 
            "💻 Execution Terminal", 
            "🔍 Ticket Deep Dive"
        ])
        
        # --- TAB 1: EXECUTIVE SUMMARY ---
        with tab_summary:
            if results:
                total_processed = len(results)
                resolved_count = sum(1 for r in results if r.get("status") == "resolved")
                escalated_count = sum(1 for r in results if r.get("status") == "escalated")
                failed_count = sum(1 for r in results if r.get("status") == "failed")
                avg_confidence = (sum(r.get("confidence", 0) for r in results) / total_processed) if total_processed else 0
                
                # Top-level metrics in a beautifully spaced container
                st.markdown("### Agent Performance Metrics")
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Total Processed", total_processed, delta="Tickets")
                m2.metric("✅ Autonomously Resolved", resolved_count, delta=f"{resolved_count/total_processed:.0%} automation", delta_color="normal")
                m3.metric("📤 Escalated to Human", escalated_count, delta=f"{escalated_count/total_processed:.0%} routing", delta_color="inverse")
                m4.metric("🎯 Average Confidence", f"{avg_confidence:.1%}")
                
                if failed_count > 0:
                    st.error(f"⚠️ {failed_count} tickets encountered internal system errors/crashes.")

                st.markdown("<br/>", unsafe_allow_html=True)
                st.markdown("### Action Breakdown")
                
                # Build dataframe for summary
                df_data = []
                for r in results:
                    cls = r.get("classification", {})
                    df_data.append({
                        "Ticket ID": r.get("ticket_id"),
                        "Intent": str(cls.get("intent", "Unknown")).replace("_", " ").title(),
                        "Status": r.get("status", "Unknown").upper(),
                        "Confidence": f"{r.get('confidence', 0):.0%}",
                        "Reason": r.get("reason", ""),
                        "Tools Chained": len(r.get("tools_used", []))
                    })
                    
                df = pd.DataFrame(df_data)
                
                # Styling the dataframe for colors based on status
                def style_status(val):
                    if val == 'RESOLVED':
                        return 'color: #00fa9a; font-weight: bold; background-color: #00331a;'
                    elif val == 'ESCALATED':
                        return 'color: #ffa500; font-weight: bold; background-color: #332200;'
                    return 'color: #ff4d4d; font-weight: bold; background-color: #330000;'
                    
                # We style the Status column to make it pop visually
                styled_df = df.style.map(style_status, subset=['Status'])
                st.dataframe(styled_df, use_container_width=True, hide_index=True)
                
            else:
                st.warning("⚠️ Could not locate audit_log.json to generate metrics.")

        # --- TAB 2: EXECUTION TERMINAL ---
        with tab_logs:
            st.markdown("### Raw Agent stdout / stderr")
            if result.stderr:
                st.error("Encountered errors during execution:")
                st.code(result.stderr, language="bash")
                
            st.code(result.stdout, language="bash")

        # --- TAB 3: TICKET DEEP DIVE ---
        with tab_deep_dive:
            if results:
                st.markdown("### Individual Ticket Reasoning Chains")
                for r in results:
                    status = r.get("status")
                    if status == "resolved":
                        icon, color = "✅", "green"
                    elif status == "escalated":
                        icon, color = "📤", "orange"
                    else:
                        icon, color = "❌", "red"
                        
                    intent_name = str(r.get('classification', {}).get('intent', 'unknown')).replace("_", " ").title()
                    conf_str = f"({r.get('confidence', 0):.0%} Confidence)"
                    
                    # Colored expandable header
                    expander_title = f"{icon} {r.get('ticket_id')} — {intent_name} {conf_str}"
                    
                    with st.expander(expander_title):
                        st.markdown(f"**Outcome Reason:** _{r.get('reason')}_")
                        tools = r.get('tools_used', [])
                        if tools:
                            st.markdown("**Tools Executed:** " + " ➔ ".join([f"`{t}`" for t in tools]))
                        st.divider()
                        st.json(r)
            else:
                st.info("No deep dive data available.")
