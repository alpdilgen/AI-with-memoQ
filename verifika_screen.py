"""
Verifika QA Tab — Streamlit UI for cloud QA over translated XLIFF.

Lives as a separate tab in app.py (Tab 4). Reads from session_state:
    - translation_results   {seg_id: target text}
    - segment_objects       {seg_id: TranslationSegment}
    - segment_match_scores  {seg_id: int}
    - last_xliff_bytes      original uploaded XLIFF (for write-back)
    - last_xliff_filename
    - detected_languages    {source, target}

Writes to session_state:
    - verifika_client       VerifikaQAClient instance (cached after login)
    - verifika_qa_profiles  list[dict]
    - verifika_issues       list[dict]
    - verifika_project_id   str
    - verifika_run_status   "idle" | "running" | "done" | "error"
    - verifika_last_error   str
"""

from __future__ import annotations

import io
from datetime import datetime
from typing import Dict, List, Optional

import streamlit as st

from services.verifika_qa_client import (
    VerifikaQAClient,
    VerifikaError,
    ISSUE_TYPE_LABELS,
    DEFAULT_BASE_URL,
)
from utils.xml_parser import XMLParser


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _init_session_state():
    defaults = {
        "verifika_client": None,
        "verifika_qa_profiles": [],
        "verifika_qa_profile_id": None,
        "verifika_issues": [],
        "verifika_project_id": None,
        "verifika_report_id": None,
        "verifika_run_status": "idle",       # idle / running / done / error
        "verifika_last_error": "",
        "verifika_progress_messages": [],
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def _get_secret(key: str, default: str = "") -> str:
    """Safely read st.secrets without raising if file is missing."""
    try:
        if hasattr(st, "secrets") and key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return default


def _build_client() -> Optional[VerifikaQAClient]:
    """
    Construct a VerifikaQAClient using credentials from secrets.

    Two credential modes — first non-empty wins:
        1. verifika_api_token  — long-lived Bearer token
        2. verifika_username + verifika_password — login flow
    """
    api_token = _get_secret("verifika_api_token")
    username = _get_secret("verifika_username")
    password = _get_secret("verifika_password")
    base_url = _get_secret("verifika_base_url", DEFAULT_BASE_URL)

    if not (api_token or (username and password)):
        st.error(
            "Verifika credentials not configured. Add either "
            "`verifika_api_token` OR `verifika_username` + "
            "`verifika_password` to Streamlit secrets."
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
    """Refresh the QA profile list from /api/QASettings."""
    try:
        profiles = client.list_qa_settings()
        st.session_state.verifika_qa_profiles = profiles
        return profiles
    except VerifikaError as e:
        st.error(f"Failed to load QA profiles: {e}")
        return []


def _severity_icon(severity: str) -> str:
    s = (severity or "").lower()
    if s in ("error", "critical", "high"):
        return "🔴"
    if s in ("warning", "medium"):
        return "🟡"
    return "🔵"


def _build_segment_list_from_state():
    """
    Return ordered (seg_id, source, target) tuples for the segments we
    just translated — used by Verifika upload (we feed it the final
    XLIFF directly, not this list, but the list helps issue mapping).
    """
    seg_objs = st.session_state.get("segment_objects", {})
    targets = st.session_state.get("translation_results", {})
    out = []
    for seg_id, seg in seg_objs.items():
        out.append((seg_id, seg.source, targets.get(seg_id, "")))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Main entry — called from app.py inside `with tab4:`
# ─────────────────────────────────────────────────────────────────────────────

def show_verifika_tab():
    """Render the Verifika QA tab. Idempotent — safe to re-run."""
    _init_session_state()

    st.subheader("✅ Verifika Cloud QA")
    st.markdown(
        "Run cloud-based quality checks against your translated XLIFF "
        "via the Verifika QA API."
    )

    # Pre-flight: do we have a translation to QA?
    has_translation = bool(st.session_state.get("translation_results"))
    if not has_translation:
        st.info("Run a translation first (Workspace tab) to enable QA.")
        return

    # Build / reuse client
    client = _get_or_create_client()
    if client is None:
        st.markdown(
            "**Setup hint:** Add the following to "
            "`.streamlit/secrets.toml` (or Streamlit Cloud secrets):\n"
            "```toml\n"
            "verifika_api_token = \"<your-token>\"\n"
            "# or, alternatively\n"
            "verifika_username = \"<user>\"\n"
            "verifika_password = \"<pass>\"\n"
            "```"
        )
        return

    # ── Step 1: QA profile selection ──────────────────────────────────────
    st.markdown("##### 1. Select QA Profile")
    col_a, col_b = st.columns([1, 4])
    with col_a:
        if st.button("🔄 Refresh profiles", use_container_width=True):
            _load_qa_profiles(client)

    if not st.session_state.verifika_qa_profiles:
        with st.spinner("Loading QA profiles from Verifika..."):
            _load_qa_profiles(client)

    profiles = st.session_state.verifika_qa_profiles
    if not profiles:
        with col_b:
            st.warning(
                "No QA profiles available. Create one first in "
                "Verifika Web/Desktop, then refresh."
            )
        return

    # Profile dropdown
    profile_labels = []
    profile_ids = []
    for p in profiles:
        pid = p.get("id") or p.get("Id") or ""
        name = p.get("name") or p.get("Name") or pid[:8]
        profile_labels.append(name)
        profile_ids.append(pid)

    default_idx = 0
    if st.session_state.verifika_qa_profile_id in profile_ids:
        default_idx = profile_ids.index(st.session_state.verifika_qa_profile_id)

    with col_b:
        chosen_label = st.selectbox(
            "Profile",
            options=profile_labels,
            index=default_idx,
            key="verifika_profile_select",
        )
    chosen_id = profile_ids[profile_labels.index(chosen_label)]
    st.session_state.verifika_qa_profile_id = chosen_id

    # ── Step 2: Run QA ────────────────────────────────────────────────────
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
        status = st.session_state.verifika_run_status
        if status == "running":
            st.info("⏳ Running…")
        elif status == "done":
            n = len(st.session_state.verifika_issues)
            st.success(f"✅ Completed — {n} issue(s) found")
        elif status == "error":
            st.error(st.session_state.verifika_last_error or "Failed")

    if run_clicked:
        _run_qa_workflow(client, chosen_id)

    # ── Step 3: Issue table & corrections ─────────────────────────────────
    issues = st.session_state.verifika_issues
    if issues:
        st.markdown("##### 3. Issues")
        _render_issue_table(issues)
        _render_apply_corrections()


# ─────────────────────────────────────────────────────────────────────────────
# Workflow runner
# ─────────────────────────────────────────────────────────────────────────────

def _run_qa_workflow(client: VerifikaQAClient, qa_settings_id: str):
    """End-to-end Verifika run — wraps client.run_full_qa with UI progress."""
    st.session_state.verifika_run_status = "running"
    st.session_state.verifika_progress_messages = []
    st.session_state.verifika_issues = []
    st.session_state.verifika_last_error = ""

    # 1. Build the translated XLIFF in memory (same as the Results tab download)
    xliff_bytes = st.session_state.get("last_xliff_bytes")
    xliff_filename = st.session_state.get("last_xliff_filename") or "translated.xliff"
    seg_objs = st.session_state.get("segment_objects", {})
    translations = st.session_state.get("translation_results", {})
    match_scores = st.session_state.get("segment_match_scores", {})

    if not xliff_bytes:
        st.session_state.verifika_run_status = "error"
        st.session_state.verifika_last_error = (
            "Original XLIFF bytes are not in session state. "
            "Please re-upload the XLIFF in the Workspace tab."
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

    # 2. Run with UI progress
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = xliff_filename.rsplit(".", 1)[0]
    project_name = f"{base}_QA_{timestamp}"
    detected = st.session_state.get("detected_languages", {})

    progress_box = st.empty()
    progress_lines: List[str] = []

    def _ui_progress(stage: str, payload: Dict):
        label_map = {
            "project_created":   "📁 Project created",
            "file_uploaded":     "⬆️ File uploaded",
            "profile_assigned":  "🎯 Profile assigned",
            "report_started":    "🚀 Report started",
            "report_progress":   "⏳ Report in progress…",
            "issues_fetched":    f"📥 {payload.get('count', 0)} issue(s) fetched",
        }
        msg = label_map.get(stage, stage)
        progress_lines.append(msg)
        progress_box.markdown("\n".join(f"- {m}" for m in progress_lines[-8:]))

    try:
        project_id, issues = client.run_full_qa(
            project_name=project_name,
            xliff_bytes=translated_xml,
            xliff_filename=xliff_filename,
            qa_settings_id=qa_settings_id,
            source_lang=detected.get("source"),
            target_lang=detected.get("target"),
            progress_cb=_ui_progress,
        )
        st.session_state.verifika_project_id = project_id
        st.session_state.verifika_issues = issues
        st.session_state.verifika_run_status = "done"
        if issues:
            st.success(f"Found {len(issues)} issue(s).")
        else:
            st.success("✅ No issues found by Verifika.")
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
# Issue table
# ─────────────────────────────────────────────────────────────────────────────

def _render_issue_table(issues: List[Dict]):
    """
    Render an editable table of Verifika issues.
    Header: Sev | Type | Seg ID | Source | Target (editable) | Suggestion
    """
    # Filters
    filt_col1, filt_col2 = st.columns(2)
    with filt_col1:
        type_options = sorted({i["issueLabel"] for i in issues})
        selected_types = st.multiselect(
            "Filter by issue type",
            options=type_options,
            default=type_options,
        )
    with filt_col2:
        sev_options = sorted({(i["severity"] or "").lower() for i in issues})
        selected_sevs = st.multiselect(
            "Filter by severity",
            options=sev_options,
            default=sev_options,
        )

    filtered = [
        i for i in issues
        if i["issueLabel"] in selected_types
        and (i["severity"] or "").lower() in selected_sevs
    ]

    st.caption(
        f"Showing {len(filtered)} of {len(issues)} issue(s). "
        "Edit a target cell and click **Apply Corrections** below."
    )

    # Header
    header_cols = st.columns([1, 2, 2, 4, 4, 3])
    for col, hdr in zip(header_cols,
                        ["", "Type", "Seg", "Source", "Target (editable)", "Suggestion"]):
        col.markdown(f"**{hdr}**")
    st.markdown("---")

    # Rows
    for idx, iss in enumerate(filtered):
        cols = st.columns([1, 2, 2, 4, 4, 3])
        cols[0].write(_severity_icon(iss["severity"]))
        cols[1].write(iss["issueLabel"])
        cols[2].code(str(iss["segmentId"]) or "—")

        src = iss["sourceText"] or ""
        cols[3].write(src[:120] + ("…" if len(src) > 120 else ""))

        # Editable target — keyed by issue id + segmentId for stability
        edit_key = f"verifika_edit_{iss['id'] or idx}_{iss['segmentId']}"
        current_target = (
            st.session_state.translation_results.get(iss["segmentId"])
            if iss["segmentId"] else iss["targetText"]
        )
        cols[4].text_input(
            "target",
            value=current_target or iss["targetText"] or "",
            key=edit_key,
            label_visibility="collapsed",
        )

        sug = iss["suggestion"] or iss["message"] or ""
        cols[5].caption(sug[:200] + ("…" if len(sug) > 200 else ""))


# ─────────────────────────────────────────────────────────────────────────────
# Apply corrections back to translation_results + downloadable XLIFF
# ─────────────────────────────────────────────────────────────────────────────

def _render_apply_corrections():
    st.markdown("---")
    apply_col, dl_col = st.columns([1, 3])
    with apply_col:
        if st.button("✅ Apply Corrections", type="primary",
                     use_container_width=True):
            _apply_corrections()

    # If we already have corrected XLIFF bytes in state, expose a download
    if st.session_state.get("verifika_corrected_xliff"):
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


def _apply_corrections():
    """
    Read every Verifika edit field, push changes into translation_results,
    rebuild XLIFF, store bytes in session for download.
    """
    issues = st.session_state.verifika_issues
    translations = st.session_state.translation_results
    applied = 0

    for idx, iss in enumerate(issues):
        edit_key = f"verifika_edit_{iss['id'] or idx}_{iss['segmentId']}"
        new_val = st.session_state.get(edit_key, "")
        if not iss["segmentId"]:
            continue
        if new_val and new_val.strip() != (translations.get(iss["segmentId"]) or "").strip():
            translations[iss["segmentId"]] = new_val
            applied += 1

    if not applied:
        st.info("No changes detected. Edit a target cell above before applying.")
        return

    st.session_state.translation_results = translations

    # Rebuild XLIFF
    seg_objs = st.session_state.get("segment_objects", {})
    match_scores = st.session_state.get("segment_match_scores", {})
    xliff_bytes = st.session_state.get("last_xliff_bytes")
    if not xliff_bytes:
        st.warning(
            f"{applied} correction(s) applied to translation_results, "
            "but original XLIFF is not in session — re-upload it in "
            "Workspace tab to enable XLIFF download."
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
