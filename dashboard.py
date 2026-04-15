from __future__ import annotations
import os
import sys
from pathlib import Path

import streamlit as st
import pandas as pd
import plotly.express as px
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / "scraper" / ".env")
sys.path.insert(0, str(Path(__file__).parent / "scraper"))
from store import LinkedInStore

CHROMA_PATH = os.getenv("CHROMA_PATH", "c:/tools/linkedin-intel/db/chroma")

st.set_page_config(page_title="LinkedIn Intel", page_icon="L", layout="wide")


@st.cache_resource
def get_store() -> LinkedInStore:
    return LinkedInStore(CHROMA_PATH)


def load_data():
    store = get_store()
    posts = store.get_all_posts()
    connections = store.get_all_connections()
    df_posts = pd.DataFrame(posts) if posts else pd.DataFrame(columns=[
        "url", "text", "author_name", "author_profile_url", "timestamp",
        "classification", "keywords_matched", "keyword_source", "reply_drafted",
    ])
    df_conn = pd.DataFrame(connections) if connections else pd.DataFrame(columns=[
        "profile_url", "name", "title", "company", "classification",
        "first_seen", "last_seen", "post_count",
    ])
    return df_posts, df_conn


df_posts, df_conn = load_data()

tab1, tab2, tab3, tab4 = st.tabs(["Overzicht", "Connecties", "Posts", "Replies"])

# ── TAB 1: OVERZICHT ──────────────────────────────────────────────────────────
with tab1:
    st.header("LinkedIn Intel — Overzicht")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Totaal posts", len(df_posts))
    col2.metric("Connecties", len(df_conn))
    if not df_posts.empty:
        col3.metric("Ideal clients", int((df_posts["classification"] == "ideal_client").sum()))
        col4.metric("Influencers", int((df_posts["classification"] == "influencer").sum()))
    else:
        col3.metric("Ideal clients", 0)
        col4.metric("Influencers", 0)

    if not df_posts.empty and "timestamp" in df_posts.columns:
        df_chart = df_posts.copy()
        df_chart["date"] = pd.to_datetime(df_chart["timestamp"], errors="coerce").dt.date
        daily = (
            df_chart.groupby(["date", "classification"])
            .size()
            .reset_index(name="count")
        )
        color_map = {
            "ideal_client": "#2196F3",
            "influencer": "#FF9800",
            "neutral": "#BDBDBD",
            "colleague": "#4CAF50",
        }
        fig = px.bar(
            daily,
            x="date",
            y="count",
            color="classification",
            title="Posts per dag",
            color_discrete_map=color_map,
        )
        st.plotly_chart(fig, use_container_width=True)

    if not df_posts.empty:
        breakdown = (
            df_posts["classification"]
            .value_counts()
            .reset_index()
            .rename(columns={"classification": "Classificatie", "count": "Aantal"})
        )
        st.dataframe(breakdown, hide_index=True, use_container_width=True)

# Remaining tabs — implemented in later tasks
with tab2:
    st.info("Connecties tab — komt in Task 4")

with tab3:
    st.info("Posts tab — komt in Task 5")

with tab4:
    st.info("Replies tab — komt in Task 6")
