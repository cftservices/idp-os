from __future__ import annotations
import os
import sys
from pathlib import Path
import json
import subprocess
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

tab1, tab2, tab3, tab4 = st.tabs(["Overzicht", "Posts", "Replies", "Contacten"])

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

with tab2:
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

        for i, (_, post) in enumerate(filtered_p.iterrows()):
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
                    p_url = post.get("url", "")
                    if post.get("reply_drafted"):
                        st.success("Reply")
                        if st.button("Ongedaan", key=f"posts_undo_{i}_{p_url}"):
                            get_store().mark_reply_drafted(p_url, drafted=False)
                            st.cache_data.clear()
                            st.rerun()
                    else:
                        if st.button("Markeer reply", key=f"posts_reply_{i}_{p_url}"):
                            get_store().mark_reply_drafted(p_url, drafted=True)
                            st.cache_data.clear()
                            st.rerun()
                st.divider()

with tab3:
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

# ── TAB 4: CONTACTEN ──────────────────────────────────────────────────────────
with tab4:
    st.header("Contacten")

    # Build combined contact list from ChromaDB connections + message store
    conversations = ms.list_conversations()
    msg_count_map: dict[str, int] = {
        c.get("profile_url", ""): len(c.get("messages", []))
        for c in conversations
    }
    last_msg_map: dict[str, str] = {
        c.get("profile_url", ""): c.get("_last_date", "")
        for c in conversations
    }

    # Get connections from ChromaDB (df_conn already loaded above)
    all_conns = df_conn.to_dict("records") if not df_conn.empty else []
    # Add contacts that exist only in message store (no ChromaDB entry)
    conn_urls = {c.get("profile_url", "") for c in all_conns}
    for conv in conversations:
        url = conv.get("profile_url", "")
        if url and url not in conn_urls:
            all_conns.append({
                "profile_url": url,
                "name": conv.get("name", "?"),
                "title": conv.get("title", ""),
                "classification": "unknown",
            })

    # Sorting: ideal_client+msgs > ideal_client > influencer+msgs > influencer > rest
    cls_rank_map = {"ideal_client": 0, "influencer": 2, "colleague": 4, "unknown": 4}

    def _contact_sort_key(c: dict) -> tuple:
        cls = c.get("classification", "unknown")
        url = c.get("profile_url", "")
        has_msgs = 1 if msg_count_map.get(url, 0) > 0 else 0
        last_msg = last_msg_map.get(url, "")
        rank = cls_rank_map.get(cls, 4) - has_msgs
        # Negate last_msg for descending date within rank group
        neg_date = "".join(chr(127 - ord(ch)) for ch in last_msg) if last_msg else "~"
        return (rank, neg_date)

    all_conns_sorted = sorted(all_conns, key=_contact_sort_key)

    col_left, col_right = st.columns([1, 2])

    with col_left:
        search_q = st.text_input("Zoek contacten", placeholder="naam of trefwoord...", key="contacten_search")

        if "selected_contact_url" not in st.session_state:
            st.session_state["selected_contact_url"] = ""

        for conn in all_conns_sorted:
            url = conn.get("profile_url", "")
            name = conn.get("name", "?")
            n_msgs = msg_count_map.get(url, 0)

            if search_q and search_q.lower() not in name.lower():
                continue

            badge = f" 💬{n_msgs}" if n_msgs > 0 else ""
            label = f"{name}{badge}"
            is_selected = st.session_state.get("selected_contact_url") == url
            btn_type = "primary" if is_selected else "secondary"
            if st.button(label, key=f"contact_btn_{url}", type=btn_type, use_container_width=True):
                st.session_state["selected_contact_url"] = url

    with col_right:
        selected_url = st.session_state.get("selected_contact_url", "")
        if not selected_url:
            st.info("Selecteer een contact links om de details te zien.")
        else:
            # Find connection data
            conn_data = next((c for c in all_conns if c.get("profile_url") == selected_url), {})
            name = conn_data.get("name", "?")
            title = conn_data.get("title", "")
            classification = conn_data.get("classification", "unknown")

            # Get about from ChromaDB connection record
            about = ""
            chroma_conn = get_store().get_connection(selected_url)
            if chroma_conn:
                about = chroma_conn.get("about", "")

            st.subheader(name)
            if title:
                st.caption(f"{title} · `{classification}`")
            st.markdown(f"[LinkedIn profiel openen]({selected_url})")
            if about:
                st.info(f"**About:** {about}")

            # Posts in DB
            if not df_posts.empty and "author_profile_url" in df_posts.columns:
                person_posts = df_posts[df_posts["author_profile_url"] == selected_url].copy()
                person_posts["timestamp"] = pd.to_datetime(person_posts["timestamp"], errors="coerce")
                person_posts = person_posts.sort_values("timestamp", ascending=False, na_position="last")
                if not person_posts.empty:
                    st.markdown(f"**Posts in DB ({len(person_posts)}):**")
                    for _, post in person_posts.head(5).iterrows():
                        date = str(post.get("timestamp", ""))[:10]
                        excerpt = str(post.get("text", ""))[:120].replace("\n", " ")
                        url_p = post.get("url", "")
                        st.markdown(f"- **{date}** — {excerpt}...  [{url_p[:60]}]({url_p})")

            st.divider()

            # Message history
            conv = ms.load_conversation(selected_url)
            messages = sorted(conv.get("messages", []), key=lambda m: m.get("date", ""))
            st.subheader("Berichtenhistorie")
            if not messages:
                st.caption("_Nog geen berichten._")
            else:
                for msg in messages:
                    mtype = msg.get("type", "?")
                    date = msg.get("date", "?")
                    content = msg.get("content", "")
                    post_url = msg.get("post_url", "")
                    msg_id = msg.get("id", "")
                    badge = "💬 comment" if mtype == "comment" else "✉️ DM"
                    col_a, col_b = st.columns([6, 1])
                    with col_a:
                        st.markdown(f"**{date}** — {badge}")
                        if post_url:
                            st.markdown(f"Op post: [{post_url[:60]}]({post_url})")
                        if msg.get("post_excerpt"):
                            st.caption(f'"{msg["post_excerpt"]}"')
                        st.write(content)
                    with col_b:
                        if msg_id and st.button("🗑", key=f"contacten_del_{msg_id}", help="Verwijder"):
                            ms.delete_message(selected_url, msg_id)
                            st.rerun()
                    st.divider()

            # New message form
            st.subheader("Nieuw bericht loggen")
            with st.form("contacten_new_msg_form", clear_on_submit=True):
                msg_type = st.radio("Type", ["comment", "dm"], horizontal=True)
                msg_post_url_input = st.text_input("Post URL (optioneel)")
                msg_post_excerpt_input = st.text_input("Post excerpt (optioneel, eerste 150 tekens)")
                msg_content_input = st.text_area("Bericht", height=100)
                msg_notes_input = st.text_input("Notities (optioneel)")
                submitted = st.form_submit_button("Opslaan")

            if submitted:
                if not msg_content_input.strip():
                    st.warning("Bericht mag niet leeg zijn.")
                else:
                    ms.save_message(
                        selected_url,
                        conn_data.get("name", ""),
                        conn_data.get("title", ""),
                        {
                            "date": datetime.now().date().isoformat(),
                            "type": msg_type,
                            "post_url": msg_post_url_input.strip(),
                            "post_excerpt": msg_post_excerpt_input.strip()[:150],
                            "content": msg_content_input.strip(),
                            "notes": msg_notes_input.strip(),
                        },
                    )
                    st.success("Bericht opgeslagen!")
                    st.rerun()

            # Clipboard context
            st.subheader("Context voor Claude Code")
            all_posts_list = df_posts.to_dict("records")
            if chroma_conn and chroma_conn.get("about"):
                for p in all_posts_list:
                    if p.get("author_profile_url") == selected_url:
                        p["about"] = chroma_conn["about"]
            ctx = ms.build_clipboard_context(selected_url, all_posts_list)
            st.text_area("Context", value=ctx, height=250, key="contacten_clipboard")
            if st.button("📋 Kopieer naar clipboard", key="contacten_copy_btn"):
                try:
                    subprocess.run("clip", input=ctx.encode("utf-8"), check=True, capture_output=True)
                    st.success("Gekopieerd!")
                except (FileNotFoundError, subprocess.CalledProcessError):
                    st.info("Selecteer tekst hierboven en kopieer handmatig.")
