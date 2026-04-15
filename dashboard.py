from __future__ import annotations
import os
import sys
from pathlib import Path
import json
from datetime import datetime

import streamlit as st
import pandas as pd
import plotly.express as px
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / "scraper" / ".env")
sys.path.insert(0, str(Path(__file__).parent / "scraper"))
sys.path.insert(0, str(Path(__file__).parent))
import message_store as ms
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

tab1, tab2, tab3, tab4, tab5 = st.tabs(["Overzicht", "Connecties", "Posts", "Replies", "Berichten"])

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
                        f"— {str(post['timestamp'])[:10] if pd.notna(post.get('timestamp')) else ''}"
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
    st.header("Reply Tracker")

    if df_posts.empty:
        st.info("Nog geen posts in de database.")
    else:
        priority = df_posts[
            df_posts["classification"].isin(["ideal_client", "influencer"])
        ].copy()
        priority["timestamp"] = pd.to_datetime(priority["timestamp"], errors="coerce")
        priority = priority.sort_values("timestamp", ascending=False, na_position="last")

        replied = priority[priority["reply_drafted"] == True]
        not_replied = priority[priority["reply_drafted"] != True]

        col1, col2 = st.columns(2)
        col1.metric("Bereikt", len(replied))
        col2.metric("Nog te doen", len(not_replied))

        st.subheader("Nog geen reply gestuurd")
        if not_replied.empty:
            st.success("Alle priority posts bereikt!")
        else:
            for i, (_, post) in enumerate(not_replied.iterrows()):
                url = post.get("url", "")
                ts = str(post["timestamp"])[:10] if pd.notna(post.get("timestamp")) else ""
                with st.expander(f"{post.get('author_name', '?')} — {ts} [{post.get('classification', '?')}]"):
                    st.write(str(post.get("text", ""))[:400])
                    if url:
                        st.markdown(f"[Bekijk post op LinkedIn]({url})")
                    if st.button("Markeer als reply gestuurd", key=f"btn_{i}_{url}"):
                        get_store().mark_reply_drafted(url, drafted=True)
                        st.cache_data.clear()
                        st.rerun()

        if not replied.empty:
            st.subheader("Al bereikt")
            for i, (_, post) in enumerate(replied.iterrows()):
                url = post.get("url", "")
                ts = str(post["timestamp"])[:10] if pd.notna(post.get("timestamp")) else ""
                col_a, col_b = st.columns([5, 1])
                col_a.markdown(
                    f"**{post.get('author_name', '?')}** — {ts} — [{str(url)[:60]}]({url})"
                )
                if col_b.button("Ongedaan", key=f"undo_{i}_{url}"):
                    get_store().mark_reply_drafted(url, drafted=False)
                    st.cache_data.clear()
                    st.rerun()

# ── TAB 5: BERICHTEN ──────────────────────────────────────────────────────────
with tab5:
    st.header("Berichten")

    # ── Person selection ──────────────────────────────────────────────────────
    conversations = ms.list_conversations()
    conv_options = ["+ Nieuwe persoon"] + [
        f"{c.get('name', '?')} ({ms.get_slug(c.get('profile_url', ''))})"
        for c in conversations
    ]
    selected = st.selectbox("Selecteer persoon", conv_options, key="msg_person_select")

    if selected == "+ Nieuwe persoon":
        new_url = st.text_input("LinkedIn profiel URL", placeholder="https://www.linkedin.com/in/username/", key="msg_new_url")
        new_name = st.text_input("Naam", key="msg_new_name")
        new_title = st.text_input("Titel / functie", key="msg_new_title")
        profile_url = new_url.strip()
        person_name = new_name.strip()
        person_title = new_title.strip()
    else:
        idx = conv_options.index(selected) - 1  # -1 for "Nieuwe persoon" offset
        conv_meta = conversations[idx]
        profile_url = conv_meta.get("profile_url", "")
        person_name = conv_meta.get("name", "")
        person_title = conv_meta.get("title", "")

    if not profile_url:
        st.info("Vul een LinkedIn profiel URL in om te beginnen.")
        st.stop()

    conv = ms.load_conversation(profile_url)
    messages = sorted(conv.get("messages", []), key=lambda m: m.get("date", ""))

    # ── Conversation timeline ─────────────────────────────────────────────────
    st.subheader(f"Conversatie — {person_name or profile_url}")
    if profile_url:
        st.markdown(f"[LinkedIn profiel openen]({profile_url})")

    if not messages:
        st.caption("_Nog geen berichten gelogd voor deze persoon._")
    else:
        for msg in messages:
            mtype = msg.get("type", "?")
            date = msg.get("date", "?")
            content = msg.get("content", "")
            post_url = msg.get("post_url", "")
            msg_id = msg.get("id", "")
            badge = "💬 comment" if mtype == "comment" else "✉️ DM"

            with st.container():
                col_a, col_b = st.columns([6, 1])
                with col_a:
                    st.markdown(f"**{date}** — {badge}")
                    if post_url:
                        st.markdown(f"Op post: [{post_url[:60]}]({post_url})")
                    st.write(content)
                with col_b:
                    if st.button("🗑", key=f"del_{msg_id}", help="Verwijder bericht"):
                        ms.delete_message(profile_url, msg_id)
                        st.rerun()
                st.divider()

    # ── New message form ──────────────────────────────────────────────────────
    st.subheader("Nieuw bericht loggen")
    with st.form("new_message_form", clear_on_submit=True):
        msg_type = st.radio("Type", ["comment", "dm"], horizontal=True, key="msg_type")
        msg_post_url = st.text_input("Post URL (optioneel)", key="msg_post_url")
        msg_content = st.text_area("Bericht", height=120, key="msg_content")
        msg_notes = st.text_input("Notities (optioneel)", key="msg_notes")
        submitted = st.form_submit_button("Opslaan")

    if submitted:
        if not msg_content.strip():
            st.warning("Bericht mag niet leeg zijn.")
        else:
            ms.save_message(
                profile_url,
                person_name,
                person_title,
                {
                    "date": datetime.now().date().isoformat(),
                    "type": msg_type,
                    "post_url": msg_post_url.strip(),
                    "post_excerpt": "",
                    "content": msg_content.strip(),
                    "notes": msg_notes.strip(),
                },
            )
            st.success("Bericht opgeslagen!")
            st.rerun()

    # ── Advies / clipboard ────────────────────────────────────────────────────
    st.subheader("Advies voor volgend bericht")
    all_posts = get_store().get_all_posts()
    ctx = ms.build_clipboard_context(profile_url, all_posts)
    st.text_area("Context voor Claude Code", value=ctx, height=300, key="msg_clipboard")
    if st.button("📋 Kopieer naar clipboard"):
        st.write(
            "<script>navigator.clipboard.writeText("
            + json.dumps(ctx)
            + ")</script>",
            unsafe_allow_html=True,
        )
        st.success("Gekopieerd! Plak in een nieuwe Claude Code chat en vraag: 'Geef advies voor mijn volgend bericht'")
