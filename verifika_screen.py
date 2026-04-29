"""
Verifika QA Tab — Streamlit UI (v3, report-based workflow).

Drives the v3 VerifikaQAClient:
    create_project → upload → create_report → run_report
    → poll /api/QualityIssues?reportId=... (statuses[] array)
    → fetch issues
    → Apply Corrections (locally + back to Verifika via
       /api/QualityIssues/updateTranslationUnits)
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

import streamlit as st
import streamlit.components.v1 as components

from services.verifika_qa_client import (
    VerifikaQAClient,
    VerifikaError,
    DEFAULT_BASE_URL,
    ISSUE_TYPE_LABELS,
)
from utils.xml_parser import XMLParser


# ─────────────────────────────────────────────────────────────────────────────
# Session-state init
# ─────────────────────────────────────────────────────────────────────────────

def _init_session_state():
    defaults = {
        "verifika_client":            None,
        "verifika_qa_profiles":       [],
        "verifika_qa_profile_id":     None,
        "verifika_project_id":        None,
        "verifika_report_id":         None,
        "verifika_issues":            [],
        "verifika_run_status":        "idle",   # idle/running/done/error
        "verifika_last_error":        "",
        "verifika_progress_messages": [],
        "verifika_corrected_xliff":   None,
        "verifika_last_statuses":     [],
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def _get_secret(key: str, default: str = "") -> str:
    try:
        if hasattr(st, "secrets") and key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return default


def _build_client() -> Optional[VerifikaQAClient]:
    api_token = _get_secret("verifika_api_token")
    username  = _get_secret("verifika_username")
    password  = _get_secret("verifika_password")
    base_url  = _get_secret("verifika_base_url", DEFAULT_BASE_URL)

    if not (api_token or (username and password)):
        st.error(
            "Verifika credentials not configured. Add to Streamlit secrets:\n"
            "    verifika_api_token = \"<token>\"\n"
            "or\n"
            "    verifika_username = \"...\"\n"
            "    verifika_password = \"...\""
        )
        return None

    client = VerifikaQAClient(
        base_url=base_url,
        api_token=api_token or None,
        username=username or None,
        password=password or None,
    )
    if username and password and not api_token:
        try:
            client.login()
        except VerifikaError as e:
            st.error(f"Verifika login failed: {e}")
            return None
    return client


def _get_or_create_client() -> Optional[VerifikaQAClient]:
    if st.session_state.verifika_client is None:
        st.session_state.verifika_client = _build_client()
    return st.session_state.verifika_client


def _load_qa_profiles(client: VerifikaQAClient) -> List[Dict]:
    try:
        profiles = client.list_qa_settings()
        st.session_state.verifika_qa_profiles = profiles
        return profiles
    except VerifikaError as e:
        st.error(f"Failed to load QA profiles: {e}")
        return []


def _severity_icon(sev: str) -> str:
    s = (sev or "").lower()
    if s in ("error", "critical", "high"):
        return "🔴"
    if s in ("warning", "medium"):
        return "🟡"
    return "🔵"


# ─────────────────────────────────────────────────────────────────────────────
# Main entry
# ─────────────────────────────────────────────────────────────────────────────

def show_verifika_tab():
    _init_session_state()

    st.subheader("✅ Verifika Cloud QA")
    st.markdown(
        "Cloud-based quality checks against the translated XLIFF via the "
        "Verifika QA API."
    )

    if not st.session_state.get("translation_results"):
        st.info("Run a translation first (Workspace tab) to enable QA.")
        return

    client = _get_or_create_client()
    if client is None:
        st.markdown(
            "**Setup:** Add to `.streamlit/secrets.toml`:\n"
            "```toml\nverifika_api_token = \"<your-token>\"\n```"
        )
        return

    # ── 1. QA profile picker ──────────────────────────────────────────────
    st.markdown("##### 1. Select QA Profile")
    refresh_col, picker_col = st.columns([1, 4])
    with refresh_col:
        if st.button("🔄 Refresh profiles", use_container_width=True):
            _load_qa_profiles(client)

    if not st.session_state.verifika_qa_profiles:
        with st.spinner("Loading QA profiles…"):
            _load_qa_profiles(client)

    profiles = st.session_state.verifika_qa_profiles
    if not profiles:
        with picker_col:
            st.warning(
                "No QA profiles available. Create one in Verifika Web/Desktop, then refresh."
            )
        return

    labels, ids = [], []
    for p in profiles:
        pid = p.get("id") or p.get("Id") or ""
        labels.append(p.get("name") or p.get("Name") or pid[:8])
        ids.append(pid)

    default_idx = 0
    if st.session_state.verifika_qa_profile_id in ids:
        default_idx = ids.index(st.session_state.verifika_qa_profile_id)

    with picker_col:
        chosen_label = st.selectbox(
            "Profile", options=labels, index=default_idx,
            key="verifika_profile_select",
        )
    chosen_id = ids[labels.index(chosen_label)]
    st.session_state.verifika_qa_profile_id = chosen_id

    # ── 2. Run ────────────────────────────────────────────────────────────
    st.markdown("##### 2. Run QA")
    run_col, status_col = st.columns([1, 4])
    with run_col:
        run_clicked = st.button(
            "▶️ Run Verifika QA",
            type="primary",
            use_container_width=True,
            disabled=st.session_state.verifika_run_status == "running",
        )

    with status_col:
        s = st.session_state.verifika_run_status
        if s == "running":
            st.info("⏳ Running…")
        elif s == "done":
            n = len(st.session_state.verifika_issues)
            st.success(f"✅ Completed — {n} issue(s) found")
        elif s == "error":
            st.error(st.session_state.verifika_last_error or "Failed")

    if run_clicked:
        _run_qa_workflow(client, chosen_id)

    # ── 3. Report viewer ──────────────────────────────────────────────────
    if st.session_state.verifika_project_id and st.session_state.verifika_run_status == "done":
        _render_report_section(client)

    # ── 4. Issue table ────────────────────────────────────────────────────
    if st.session_state.verifika_issues:
        st.markdown("##### 4. Issues (editable)")
        _render_issue_table(st.session_state.verifika_issues)
        _render_apply_corrections(client)


# ─────────────────────────────────────────────────────────────────────────────
# Workflow runner
# ─────────────────────────────────────────────────────────────────────────────

def _run_qa_workflow(client: VerifikaQAClient, qa_settings_id: str):
    st.session_state.verifika_run_status = "running"
    st.session_state.verifika_progress_messages = []
    st.session_state.verifika_issues = []
    st.session_state.verifika_last_error = ""
    st.session_state.verifika_corrected_xliff = None
    st.session_state.verifika_last_statuses = []

    xliff_bytes    = st.session_state.get("last_xliff_bytes")
    xliff_filename = st.session_state.get("last_xliff_filename") or "translated.xliff"
    seg_objs       = st.session_state.get("segment_objects", {})
    translations   = st.session_state.get("translation_results", {})
    match_scores   = st.session_state.get("segment_match_scores", {})

    if not xliff_bytes:
        st.session_state.verifika_run_status = "error"
        st.session_state.verifika_last_error = (
            "Original XLIFF bytes are not in session. "
            "Re-upload the XLIFF in Workspace tab."
        )
        st.error(st.session_state.verifika_last_error)
        return

    try:
        translated_xml = XMLParser.update_xliff(
            xliff_bytes, translations, seg_objs, match_scores=match_scores,
        )
    except Exception as e:
        st.session_state.verifika_run_status = "error"
        st.session_state.verifika_last_error = f"XLIFF rebuild failed: {e}"
        st.error(st.session_state.verifika_last_error)
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = xliff_filename.rsplit(".", 1)[0]
    project_name = f"{base}_QA_{timestamp}"

    progress_box = st.empty()
    msgs: List[str] = []

    label_map = {
        "project_created":      "📁 Project created",
        "file_uploaded":        "⬆️ File uploaded",
        "project_started":      "🟢 Project started (task created)",
        "task_ready":           "📋 Task ready",
        "tasks_accepted":       "✅ Task accepted",
        "report_created":       "📊 Report created",
        "qa_check_triggered":   "⭐ QA check triggered",
        "report_started":       "🚀 QA analysis started (fallback)",
        "qa_completed":         "🎯 QA analysis completed",
        "issues_fetched":       "📥 Issues fetched",
    }

    def _ui_progress(stage: str, payload: Dict):
        if stage == "qa_progress":
            statuses = payload.get("statuses") or []
            st.session_state.verifika_last_statuses = statuses
            done = sum(1 for s in statuses if int(s.get("status", 0) or 0) == 1)
            total = len(statuses)
            issues_so_far = len(payload.get("qualityIssues") or [])
            # Build per-category breakdown
            cats = []
            for s in statuses:
                t = s.get("issueType")
                done_one = int(s.get("status", 0) or 0) == 1
                label = ISSUE_TYPE_LABELS.get(t, f"T{t}")
                cats.append(f"{'✓' if done_one else '⏳'} {label}")
            cat_str = " · ".join(cats)
            msg = (f"⏳ Polling… {done}/{total} categories ready, "
                   f"{issues_so_far} issue(s) found so far\n  {cat_str}")
        elif stage == "issues_fetched":
            msg = f"📥 {payload.get('count', 0)} issue(s) fetched"
        elif stage == "qa_completed":
            statuses = payload.get("statuses") or []
            issues = payload.get("qualityIssues") or []
            msg = (f"🎯 QA completed — "
                   f"{len(statuses)} categories all ready, "
                   f"{len(issues)} issue(s)")
        else:
            msg = label_map.get(stage, stage)
        msgs.append(msg)
        progress_box.markdown("\n".join(f"- {m}" for m in msgs[-12:]))

    try:
        project_id, report_id, issues = client.run_full_qa(
            project_name=project_name,
            xliff_bytes=translated_xml,
            xliff_filename=xliff_filename,
            qa_settings_id=qa_settings_id,
            progress_cb=_ui_progress,
        )
        st.session_state.verifika_project_id = project_id
        st.session_state.verifika_report_id  = report_id
        st.session_state.verifika_issues     = issues
        st.session_state.verifika_run_status = "done"
        if issues:
            st.success(f"Found {len(issues)} issue(s).")
        else:
            st.success("✅ No issues reported by Verifika.")

    except VerifikaError as e:
        st.session_state.verifika_run_status = "error"
        st.session_state.verifika_last_error = str(e)
        st.error(f"Verifika error: {e}")
        if e.response_body:
            with st.expander("Response details"):
                st.code(e.response_body)
    except Exception as e:
        st.session_state.verifika_run_status = "error"
        st.session_state.verifika_last_error = f"Unexpected error: {e}"
        st.error(st.session_state.verifika_last_error)


# ─────────────────────────────────────────────────────────────────────────────
# Report viewer (link + iframe attempt)
# ─────────────────────────────────────────────────────────────────────────────

def _render_report_section(client: VerifikaQAClient):
    st.markdown("##### 3. Verifika Report")
    project_id = st.session_state.verifika_project_id
    url = client.report_url(project_id)

    cols = st.columns([2, 2, 1])
    with cols[0]:
        st.markdown(
            f"🔗 [Open in Verifika (new tab)]({url})",
            help="Opens Verifika's review screen in a new tab. "
                 "Requires you to be logged into Verifika in the same browser."
        )
    with cols[1]:
        show_iframe = st.toggle(
            "Try inline iframe (often fails — see notes)",
            value=False,
            key="verifika_iframe_toggle",
            help=(
                "Verifika's auth provider blocks iframe embedding "
                "(X-Frame-Options on auth.e-verifika.com). The iframe "
                "will likely show an error. The 'Open in new tab' link "
                "is the reliable option."
            ),
        )

    if show_iframe:
        components.iframe(url, height=900, scrolling=True)


# ─────────────────────────────────────────────────────────────────────────────
# Issue table & corrections
# ─────────────────────────────────────────────────────────────────────────────

def _render_issue_table(issues: List[Dict]):
    if not issues:
        return

    # Filters
    f1, f2 = st.columns(2)
    with f1:
        type_options = sorted({i["issueLabel"] for i in issues})
        selected_types = st.multiselect(
            "Filter by issue type", options=type_options,
            default=type_options,
        )
    with f2:
        show_ignored = st.checkbox("Show ignored issues", value=False)

    filtered = [
        i for i in issues
        if i["issueLabel"] in selected_types
        and (show_ignored or not i.get("isIgnored"))
    ]

    st.caption(
        f"Showing {len(filtered)} of {len(issues)} issue(s). "
        "Edit a target cell, then click **Apply Corrections**."
    )

    header_cols = st.columns([1, 2, 2, 4, 4, 3])
    for col, hdr in zip(header_cols,
                        ["", "Type", "Seg", "Source",
                         "Target (editable)", "Detail"]):
        col.markdown(f"**{hdr}**")
    st.markdown("---")

    for idx, iss in enumerate(filtered):
        cols = st.columns([1, 2, 2, 4, 4, 3])
        cols[0].write(_severity_icon(iss["severity"]))
        cols[1].write(iss["issueLabel"])
        cols[2].code(str(iss["segmentId"]) or "—")

        src = iss["sourceText"] or ""
        cols[3].write(src[:120] + ("…" if len(src) > 120 else ""))

        edit_key = f"verifika_edit_{iss['id'] or idx}_{iss['segmentId']}"
        # Prefer the current Streamlit translation; fall back to Verifika's view
        current = (
            st.session_state.translation_results.get(iss["segmentId"])
            if iss["segmentId"]
            else iss["targetText"]
        )
        cols[4].text_input(
            "target",
            value=current or iss["targetText"] or "",
            key=edit_key,
            label_visibility="collapsed",
        )

        # Detail column — issueKind text + ignored flag if present
        bits = []
        if iss.get("issueKind"):
            bits.append(str(iss["issueKind"]))
        if iss.get("isIgnored"):
            bits.append("🚫 ignored")
        cols[5].caption(" · ".join(bits) if bits else "—")


def _render_apply_corrections(client: VerifikaQAClient):
    st.markdown("---")
    apply_col, dl_col = st.columns([1, 3])

    sync_to_verifika = st.checkbox(
        "Also push corrections to Verifika "
        "(POST /api/QualityIssues/updateTranslationUnits)",
        value=True,
        help="When checked, the same corrections will be sent to Verifika "
             "so the report tracks the changes.",
    )

    with apply_col:
        if st.button("✅ Apply Corrections",
                     type="primary", use_container_width=True):
            _apply_corrections(client, sync_to_verifika)

    if st.session_state.verifika_corrected_xliff:
        with dl_col:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            base_name = (st.session_state.get("last_xliff_filename") or "translated").rsplit(".", 1)[0]
            ext = (st.session_state.get("last_xliff_filename") or ".xliff").rsplit(".", 1)[-1]
            st.download_button(
                "⬇️ Download QA-Corrected XLIFF",
                st.session_state.verifika_corrected_xliff,
                file_name=f"{base_name}_qa_corrected_{ts}.{ext}",
                mime="application/xml",
                use_container_width=True,
            )


def _apply_corrections(client: VerifikaQAClient, sync_to_verifika: bool):
    issues = st.session_state.verifika_issues
    translations = st.session_state.translation_results
    applied = 0
    verifika_updates: List[Dict] = []

    for idx, iss in enumerate(issues):
        edit_key = f"verifika_edit_{iss['id'] or idx}_{iss['segmentId']}"
        new_val = st.session_state.get(edit_key, "")
        if not iss["segmentId"]:
            continue
        old_val = (translations.get(iss["segmentId"]) or "").strip()
        if new_val and new_val.strip() != old_val:
            translations[iss["segmentId"]] = new_val
            applied += 1
            tu_id = iss.get("translationUnitId")
            if sync_to_verifika and tu_id:
                verifika_updates.append({
                    "id": tu_id,
                    "text": new_val,
                    "originalText": iss.get("originalTarget") or iss.get("targetText"),
                })

    if not applied:
        st.info("No changes detected. Edit a target cell before applying.")
        return

    st.session_state.translation_results = translations

    # Push to Verifika (best-effort; failure here doesn't block local download)
    if sync_to_verifika and verifika_updates and st.session_state.verifika_report_id:
        try:
            client.update_translation_units(
                st.session_state.verifika_report_id, verifika_updates
            )
            st.success(f"📤 Pushed {len(verifika_updates)} correction(s) to Verifika.")
        except VerifikaError as e:
            st.warning(f"Local apply succeeded but Verifika sync failed: {e}")

    # Rebuild XLIFF for local download
    seg_objs       = st.session_state.get("segment_objects", {})
    match_scores   = st.session_state.get("segment_match_scores", {})
    xliff_bytes    = st.session_state.get("last_xliff_bytes")

    if not xliff_bytes:
        st.warning(
            f"{applied} correction(s) saved to translation_results, "
            "but original XLIFF is not in session — re-upload it in Workspace tab."
        )
        return

    try:
        corrected = XMLParser.update_xliff(
            xliff_bytes, translations, seg_objs, match_scores=match_scores,
        )
        st.session_state.verifika_corrected_xliff = corrected
        st.success(
            f"✅ {applied} correction(s) applied. "
            "Use the download button on the right to get the corrected XLIFF."
        )
    except Exception as e:
        st.error(f"Failed to rebuild XLIFF: {e}")
