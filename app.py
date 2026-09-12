# """
# EdgeDash Dashboard - Streamlit interface for career intelligence.

# Read-only dashboard per rule 49. Never runs a cycle.
# Robust to hostile startup per rule 50.
# """
"""
EdgeDash Dashboard - Streamlit interface for career intelligence.

Dashboard displays career intelligence and allows users to update
job-search preferences. It never runs a cycle.
Robust to hostile startup per rule 50.
"""
import logging
import os
import subprocess
import sys
from pathlib import Path

import streamlit as st

from edgedash import storage
from edgedash.config import load_config, save_preferences
from edgedash.orchestrator import run_cycle

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

def get_db_status():
    """Check database availability and return the active backend."""
    try:
        config = load_config()
        # st.write("DEBUG DATABASE_URL loaded:", bool(os.getenv("DATABASE_URL")))
        # st.write("DEBUG Storage backend:", storage.get_backend_status(config.db_path))

        backend, error = storage.get_backend_status(config.db_path)

        if backend == "postgres":
            return True, "Supabase", None

        if backend == "sqlite":
            return True, "SQLite", None

        return False, "Unavailable", error

    except Exception as exc:
        logger.error("Database status check failed: %s", exc)
        return False, "Unavailable", str(exc)


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

            if unscored > 0:
                with st.expander("Why are these jobs unscored?"):
                    st.write(
                        f"{unscored} job listing(s) were fetched successfully, "
                        "but could not be scored during the last cycle."
                    )
                    st.write(
                        "The AI scoring service was temporarily unavailable "
                        "while processing these listings. They remain in the "
                        "database and can be scored during a later cycle."
                    )

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
        min_score = config.min_fit_score

        matches = storage.get_scored_listings(
            config.db_path,
            min_score=min_score,
        )

        if matches:
            for match in matches[:5]:
                with st.expander(
                    f"{match['title']} at {match['company']} "
                    f"- Score: {match['fit_score']}"
                ):
                    st.write(
                        match.get(
                            "fit_reason",
                            "No reason available",
                        )
                    )
        else:
            st.info(
                f"No matches above your minimum fit score of "
                f"{min_score}."
            )

            st.caption(
                "Jobs may have been scored successfully, but their "
                "fit scores did not meet your selected threshold."
            )

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
    db_ok, db_backend, db_error = get_db_status()

    if not db_ok:
        st.error("🔴 Database unavailable")
        st.info(
            "Neither Supabase nor the local SQLite database "
            "is currently available."
        )

        if db_error:
            logger.error("Database error: %s", db_error)

        st.stop()
    
    # Load config
    try:
        config = load_config()
    except Exception as e:
        st.error("Configuration error")
        logger.error(f"Config load failed: {e}")
        st.stop()

    # -----------------------------------------------------------------------
    # Active Database Status
    # -----------------------------------------------------------------------
    if db_backend == "Supabase":
        st.success("🟢 **Database: Supabase — Active**")

    elif db_backend == "SQLite":
        st.warning(
            "🟡 **Database: SQLite (`edgedash.db`) — Active**\n\n"
            "Supabase is currently unavailable, so EdgeDash is using "
            "the local SQLite fallback."
        )    
    # -----------------------------------------------------------------------
    # Job Search Preferences
    # -----------------------------------------------------------------------
    with st.sidebar:
        st.header("Job Search Preferences")

        with st.form("job_preferences_form"):
            target_role = st.text_input(
                "Target Role",
                value=config.target_role,
            )

            target_city = st.text_input(
                "Target City",
                value=config.target_city,
            )

            seniority_options = ["junior", "mid", "senior"]
            current_seniority = (
                config.target_seniority
                if config.target_seniority in seniority_options
                else "mid"
            )

            target_seniority = st.selectbox(
                "Seniority",
                seniority_options,
                index=seniority_options.index(current_seniority),
            )

            experience_years = st.number_input(
                "Experience (years)",
                min_value=0,
                max_value=50,
                value=config.experience_years,
                step=1,
            )

            prefer_remote = st.checkbox(
                "Prefer Remote",
                value=config.prefer_remote,
            )

            min_fit_score = st.slider(
                "Minimum Fit Score",
                min_value=0,
                max_value=100,
                value=config.min_fit_score,
                step=5,
            )

            skills_text = st.text_area(
                "My Skills",
                value=", ".join(config.my_skills),
                help="Enter skills separated by commas.",
            )

            keywords_text = st.text_area(
                "Search Keywords",
                value=", ".join(config.keywords),
                help="Enter keywords separated by commas.",
            )
# --------------------------
            search_jobs = st.form_submit_button(
                "🔍 Search Jobs",
                type="primary",
                use_container_width=True,
            )

        if search_jobs:
            skills = [
                skill.strip()
                for skill in skills_text.split(",")
                if skill.strip()
            ]

            keywords = [
                keyword.strip()
                for keyword in keywords_text.split(",")
                if keyword.strip()
            ]

            try:
                save_preferences(
                    target_role=target_role,
                    target_city=target_city,
                    target_seniority=target_seniority,
                    prefer_remote=prefer_remote,
                    keywords=keywords,
                    my_skills=skills,
                    experience_years=experience_years,
                    min_fit_score=min_fit_score,
                )

                storage.clear_job_search_data(config.db_path)

                # Run fresh job-search cycle here             
                st.info("🔍 Searching for new jobs...")

                fresh_config = load_config()

                run_cycle(
                    fresh_config,
                    dry_run=False,
                    force_agents=["fetcher", "scorer", "gap_analyzer"],
                )

                st.success("✅ New job search completed!")
                st.rerun()

            except Exception as exc:
                logger.error(f"Failed to start job search: {exc}")
                st.error(f"Could not start job search: {exc}")
    # --------------------------------------------------    
    
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
        github_url = getattr(config, "github_url", "")
        if github_url:
            st.markdown(f"[GitHub]({github_url})")


if __name__ == "__main__":
    main()
