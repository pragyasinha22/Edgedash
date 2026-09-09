"""
EdgeDash Dashboard - Streamlit interface for career intelligence.

Read-only dashboard per rule 49. Never runs a cycle.
Robust to hostile startup per rule 50.
"""

import logging
import os
from datetime import datetime, timezone

import streamlit as st

from edgedash import storage
from edgedash.config import load_config

# Configure logging - never log secrets
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Page config
st.set_page_config(
    page_title="EdgeDash",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# Database connection with error handling
# ---------------------------------------------------------------------------

@st.cache_resource
def get_db_status():
    """Check database connection status without exposing secrets."""
    try:
        config = load_config()
        # Test connection
        with storage._connect(config.db_path) as conn:
            conn.execute("SELECT 1")
        return True, "Connected", None
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        return False, str(e), None


# ---------------------------------------------------------------------------
# Safe panel wrappers - one failing panel cannot take down the page
# ---------------------------------------------------------------------------

def safe_panel(title, content_func):
    """Wrap a panel in try/except so failures don't crash the page."""
    try:
        st.subheader(title)
        content_func()
    except Exception as e:
        logger.error(f"Panel '{title}' failed: {e}")
        st.warning(f"Could not load {title.lower()}")


# ---------------------------------------------------------------------------
# Panel content functions
# ---------------------------------------------------------------------------

def render_status_panel(config):
    """Render system status panel."""
    col1, col2, col3 = st.columns(3)
    
    with col1:
        try:
            last_cycle_time = storage.last_cycle_time(config.db_path)
            st.metric("Last Cycle", last_cycle_time or "Never")
        except Exception as e:
            logger.error(f"Failed to get last cycle time: {e}")
            st.metric("Last Cycle", "Error")
    
    with col2:
        try:
            last_cycle_status = storage.last_cycle_status(config.db_path)
            st.metric("Status", last_cycle_status or "Unknown")
        except Exception as e:
            logger.error(f"Failed to get last cycle status: {e}")
            st.metric("Status", "Error")
    
    with col3:
        try:
            unscored = storage.count_unscored(config.db_path)
            st.metric("Unscored Listings", unscored)
        except Exception as e:
            logger.error(f"Failed to count unscored: {e}")
            st.metric("Unscored Listings", "Error")


def render_gaps_panel(config):
    """Render skill gaps panel."""
    try:
        gaps = storage.get_latest_gap_snapshot(config.db_path)
        if gaps:
            for gap in gaps[:5]:
                with st.expander(f"{gap['skill']} - {gap['listings_blocked']} listings blocked"):
                    st.write(f"Opportunity cost: {gap['opportunity_cost']:.2f}")
                    st.write(f"Mean score: {gap['mean_score']:.2f}")
        else:
            st.info("No gap data available yet. First cycle will compute gaps.")
    except Exception as e:
        logger.error(f"Failed to load gaps: {e}")
        st.warning("Could not load gap data")


def render_matches_panel(config):
    """Render best matches panel."""
    try:
        matches = storage.get_scored_listings(config.db_path, min_score=70)
        if matches:
            for match in matches[:5]:
                with st.expander(f"{match['title']} at {match['company']} - Score: {match['fit_score']}"):
                    st.write(match.get('fit_reason', 'No reason available'))
        else:
            st.info("No scored listings yet.")
    except Exception as e:
        logger.error(f"Failed to load matches: {e}")
        st.warning("Could not load matches")


from edgedash.query.ask import ask


def render_ask_panel(config):

    question = st.text_input(
        "Ask a question about your data",
        placeholder="e.g. Which projects have the most open tasks?"
    )

    if st.button("Ask", type="primary"):
        if not question.strip():
            st.warning("Please enter a question.")
            return

        with st.spinner("Analyzing your data..."):
            try:
                result = ask(question.strip())

                if result.text:
                    st.write(result.text)
                else:
                    st.warning("No answer was returned.")

            except Exception as exc:
                st.error(f"Unable to answer the question: {exc}")


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------

def main():
    """Main dashboard application."""
    
    # Check database status first
    db_ok, db_message, _ = get_db_status()
    
    if not db_ok:
        st.error("Database not configured or unreachable")
        st.info("Please set DATABASE_URL environment variable and try again.")
        st.stop()
    
    # Load config
    try:
        config = load_config()
    except Exception as e:
        st.error("Configuration error")
        logger.error(f"Config load failed: {e}")
        st.stop()
    
    # Header
    st.title("EdgeDash")
    st.markdown(f"**Target Role:** {config.target_role} in {config.target_city}")
    
    # Get last cycle time for footer
    try:
        last_cycle_time = storage.last_cycle_time(config.db_path)
    except Exception as e:
        logger.error(f"Failed to get last cycle time: {e}")
        last_cycle_time = None
    
    # Render panels with safe wrappers
    safe_panel("System Status", lambda: render_status_panel(config))
    safe_panel("Skill Gaps", lambda: render_gaps_panel(config))
    safe_panel("Best Matches", lambda: render_matches_panel(config))
    safe_panel("Ask Your Data", lambda: render_ask_panel(config))
    
    # Footer
    st.divider()
    
    col_left, col_right = st.columns([3, 1])
    
    with col_left:
        if last_cycle_time:
            st.caption(f"Last successful cycle: {last_cycle_time}")
        else:
            st.caption("No successful cycles yet")
    
    with col_right:
        st.markdown("[GitHub](https://github.com/yourusername/edgedash)")


if __name__ == "__main__":
    main()
