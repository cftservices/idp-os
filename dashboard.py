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


@st.cache_data(ttl=300)
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
    st.header("Connecties")

    if df_conn.empty:
        st.info("Nog geen connecties in de database. Run scraper eerst.")
    else:
        cls_options = sorted(df_conn["classification"].dropna().unique().tolist())
        cls_filter = st.multiselect(
            "Filter op classificatie",
            options=cls_options,
            default=cls_options,
            key="conn_cls_filter",
        )
        filtered_conn = df_conn[df_conn["classification"].isin(cls_filter)].copy()

        # Sort: ideal_client first, then influencer, then rest
        cls_order = {"ideal_client": 0, "influencer": 1, "colleague": 2, "unknown": 3}
        filtered_conn["_sort"] = filtered_conn["classification"].map(cls_order).fillna(9)
        filtered_conn = filtered_conn.sort_values("_sort").drop(columns=["_sort"])

        for _, row in filtered_conn.iterrows():
            label = f"{row.get('name', '?')} — {str(row.get('title', ''))[:80]} [{row.get('classification', '?')}]"
            with st.expander(label):
                col1, col2, col3 = st.columns(3)
                col1.write(f"**Eerste gezien:** {row.get('first_seen', '?')}")
                col2.write(f"**Laatste activiteit:** {row.get('last_seen', '?')}")
                col3.write(f"**Posts in DB:** {row.get('post_count', 0)}")

                profile_url = row.get("profile_url", "")
                if profile_url:
                    st.markdown(f"[LinkedIn profiel openen]({profile_url})")

                # Posts van deze connectie
                if not df_posts.empty and "author_profile_url" in df_posts.columns:
                    conn_posts = df_posts[df_posts["author_profile_url"] == profile_url].copy()
                    conn_posts["timestamp"] = pd.to_datetime(conn_posts["timestamp"], errors="coerce")
                    conn_posts = conn_posts.sort_values("timestamp", ascending=False, na_position="last")

                    if not conn_posts.empty:
                        st.write(f"**{len(conn_posts)} post(s) gevonden:**")
                        for _, post in conn_posts.iterrows():
                            kw = ", ".join(post.get("keywords_matched") or [])
                            reply_badge = "Reply gestuurd" if post.get("reply_drafted") else "Geen reply"
                            st.markdown(
                                f"- **{str(post.get('timestamp', ''))[:10]}** "
                                f"— {str(post.get('text', ''))[:200]}  \n"
                                f"  [{str(post.get('url', ''))[:70]}]({post.get('url', '')}) "
                                f"| `{kw}` | *{reply_badge}*"
                            )
                    else:
                        st.caption("_Geen posts gevonden voor dit profiel._")

with tab3:
    st.header("Posts")

    if df_posts.empty:
        st.info("Nog geen posts in de database.")
    else:
        col1, col2, col3 = st.columns(3)
        with col1:
            cls_opts = sorted(df_posts["classification"].dropna().unique().tolist())
            cls_filter_p = st.multiselect(
                "Classificatie",
                options=cls_opts,
                default=[c for c in ["ideal_client", "influencer"] if c in cls_opts],
                key="posts_cls",
            )
        with col2:
            kw_opts = sorted(df_posts["keyword_source"].dropna().unique().tolist()) if "keyword_source" in df_posts.columns else []
            kw_filter = st.multiselect("Keyword source", options=kw_opts, default=kw_opts, key="posts_kw")
        with col3:
            search = st.text_input("Zoek in tekst", placeholder="bijv. MQTT broker...")

        filtered_p = df_posts.copy()
        if cls_filter_p:
            filtered_p = filtered_p[filtered_p["classification"].isin(cls_filter_p)]
        if kw_filter:
            filtered_p = filtered_p[filtered_p["keyword_source"].isin(kw_filter)]
        if search:
            filtered_p = filtered_p[
                filtered_p["text"].str.contains(search, case=False, na=False)
            ]

        filtered_p = filtered_p.copy()
        filtered_p["timestamp"] = pd.to_datetime(filtered_p["timestamp"], errors="coerce")
        filtered_p = filtered_p.sort_values("timestamp", ascending=False, na_position="last")

        st.write(f"**{len(filtered_p)} posts**")

        for _, post in filtered_p.iterrows():
            kw = ", ".join(post.get("keywords_matched") or [])
            with st.container():
                col_a, col_b = st.columns([4, 1])
                with col_a:
                    st.markdown(
                        f"**{post.get('author_name', '?')}** "
                        f"— `{post.get('classification', '?')}` "
                        f"— {str(post.get('timestamp', ''))[:10]}"
                    )
                    st.write(str(post.get("text", ""))[:350])
                    url = post.get("url", "")
                    if url:
                        st.markdown(f"[Bekijk post]({url}) | Keywords: `{kw}`")
                with col_b:
                    if post.get("reply_drafted"):
                        st.success("Reply gestuurd")
                st.divider()

with tab4:
    st.info("Replies tab — komt in Task 6")
