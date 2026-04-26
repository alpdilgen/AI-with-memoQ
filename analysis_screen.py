"""
ANALYSIS SCREEN - Post-translation TM match analysis
Displays accurate segment and word counts by fuzzy match level.
"""

import streamlit as st
import pandas as pd


def show_analysis_screen(analysis_results):
    """Show TM match analysis after translation completes."""

    total_segments = analysis_results['total_segments']
    total_words = analysis_results['total_words']
    by_level = analysis_results['by_level']

    # --- Summary metrics ---
    bypass_segs = (
        by_level.get('101% (Context)', {}).get('segments', 0) +
        by_level.get('100%', {}).get('segments', 0) +
        by_level.get('95%-99%', {}).get('segments', 0)
    )
    fuzzy_segs = sum(
        by_level.get(level, {}).get('segments', 0)
        for level in ['85%-94%', '75%-84%', '50%-74%']
    )
    no_match_segs = by_level.get('No match', {}).get('segments', 0)

    bypass_words = (
        by_level.get('101% (Context)', {}).get('words', 0) +
        by_level.get('100%', {}).get('words', 0) +
        by_level.get('95%-99%', {}).get('words', 0)
    )
    fuzzy_words = sum(
        by_level.get(level, {}).get('words', 0)
        for level in ['85%-94%', '75%-84%', '50%-74%']
    )
    no_match_words = by_level.get('No match', {}).get('words', 0)

    tm_seg_cov = (bypass_segs + fuzzy_segs) / max(total_segments, 1) * 100
    tm_word_cov = (bypass_words + fuzzy_words) / max(total_words, 1) * 100

    st.markdown("### TM Match Analysis")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("📊 Total Segments", total_segments)
    with col2:
        st.metric("📝 Total Words", total_words)
    with col3:
        st.metric("🎯 TM Segment Coverage", f"{tm_seg_cov:.1f}%")
    with col4:
        st.metric("📖 TM Word Coverage", f"{tm_word_cov:.1f}%")

    st.markdown("---")
    st.markdown("#### Match Distribution")

    LEVELS = [
        '101% (Context)',
        '100%',
        '95%-99%',
        '85%-94%',
        '75%-84%',
        '50%-74%',
        'No match',
    ]

    breakdown_data = []
    for level in LEVELS:
        data = by_level.get(level, {'segments': 0, 'words': 0})
        segs = data['segments']
        words = data['words']
        seg_pct = segs / max(total_segments, 1) * 100
        word_pct = words / max(total_words, 1) * 100
        breakdown_data.append({
            'Match Level': level,
            'Segments': segs,
            'Seg %': f"{seg_pct:.1f}%",
            'Words': words,
            'Word %': f"{word_pct:.1f}%",
        })

    # Totals row
    breakdown_data.append({
        'Match Level': 'TOTAL',
        'Segments': total_segments,
        'Seg %': '100.0%',
        'Words': total_words,
        'Word %': '100.0%',
    })

    df = pd.DataFrame(breakdown_data)
    st.dataframe(df, width="stretch", hide_index=True)

    st.markdown("---")
    st.markdown("#### Processing Summary")

    summary_data = [
        {
            'Category': 'Leveraged (≥95% — used verbatim from TM)',
            'Segments': bypass_segs,
            'Seg %': f"{bypass_segs / max(total_segments, 1) * 100:.1f}%",
            'Words': bypass_words,
            'Word %': f"{bypass_words / max(total_words, 1) * 100:.1f}%",
        },
        {
            'Category': 'Fuzzy (50–94% — TM context sent to LLM)',
            'Segments': fuzzy_segs,
            'Seg %': f"{fuzzy_segs / max(total_segments, 1) * 100:.1f}%",
            'Words': fuzzy_words,
            'Word %': f"{fuzzy_words / max(total_words, 1) * 100:.1f}%",
        },
        {
            'Category': 'No match (<50% — LLM only)',
            'Segments': no_match_segs,
            'Seg %': f"{no_match_segs / max(total_segments, 1) * 100:.1f}%",
            'Words': no_match_words,
            'Word %': f"{no_match_words / max(total_words, 1) * 100:.1f}%",
        },
    ]

    summary_df = pd.DataFrame(summary_data)
    st.dataframe(summary_df, width="stretch", hide_index=True)

    st.markdown("---")


# ─────────────────────────────────────────────────────────────────────────────
# QA SECTION
# ─────────────────────────────────────────────────────────────────────────────

def show_qa_section():
    """
    QA Check panel shown in Tab 2 (Analysis/Results).

    Reads from st.session_state:
      - segment_objects        : Dict[str, TranslationSegment]
      - translation_results    : Dict[str, str]
      - memoq_client           : MemoQServerClient | None
      - selected_tb_guids      : List[str]
      - detected_languages     : {source, target}
      - xliff_bytes            : bytes | None  (original XLIFF for write-back)
      - xliff_filename         : str | None
      - segment_match_scores   : Dict[str, float]
      - qa_issues              : List[QAIssue]  (populated after Run QA)

    Writes to st.session_state:
      - qa_issues
      - translation_results   (when Apply Corrections is clicked)
    """
    import streamlit as st
    from services.qa_engine import QAEngine, QAIssue
    from utils.xml_parser import XMLParser

    st.markdown("---")
    st.markdown("### \U0001f50d QA Check")

    # ── Check toggles ──────────────────────────────────────────────────────
    st.markdown("**Checks to run:**")
    cols = st.columns(6)
    check_labels = {
        "terminology":  "\U0001f4da Terminology",
        "numbers":      "\U0001f522 Numbers",
        "tags":         "\U0001f3f7 Tags",
        "empty":        "\u274c Empty",
        "punctuation":  "\U0001f539 Punctuation",
        "consistency":  "\U0001f501 Consistency",
    }
    enabled = []
    for i, (key, label) in enumerate(check_labels.items()):
        ss_key = f"qa_check_{key}"
        if ss_key not in st.session_state:
            st.session_state[ss_key] = True
        with cols[i]:
            if st.checkbox(label, value=st.session_state[ss_key], key=ss_key):
                enabled.append(key)

    # ── Run QA button ─────────────────────────────────────────────────────
    col_btn, col_info = st.columns([1, 4])
    with col_btn:
        run_qa = st.button("\U0001f50d Run QA", type="primary", use_container_width=True)

    if run_qa:
        segment_objects   = st.session_state.get("segment_objects", {})
        translation_results = st.session_state.get("translation_results", {})
        memoq_client      = st.session_state.get("memoq_client")
        tb_guids          = st.session_state.get("selected_tb_guids", [])
        detected_langs    = st.session_state.get("detected_languages", {})
        src_lang          = detected_langs.get("source")
        tgt_lang          = detected_langs.get("target")

        if not translation_results:
            st.warning("No translation results found. Run translation first.")
            st.stop()

        # Build ordered segments list — use segment_objects + translation_results
        from models.entities import TranslationSegment
        seg_list = []
        for seg_id, seg_obj in segment_objects.items():
            translated = translation_results.get(seg_id, "")
            seg_list.append(TranslationSegment(
                id=seg_id,
                source=seg_obj.source,
                target=translated,
                tag_map=seg_obj.tag_map,
            ))

        # Terminology: fetch TB terms if client + TB selected + check enabled
        tb_terms = None
        if "terminology" in enabled and memoq_client and tb_guids:
            with st.spinner("Fetching TB terms from memoQ..."):
                sources = [s.source for s in seg_list]
                all_hits: dict = {}
                for tb_guid in tb_guids:
                    try:
                        hits = memoq_client.lookup_terms(
                            tb_guid=tb_guid,
                            segments=sources,
                            src_lang=src_lang,
                            tgt_lang=tgt_lang,
                        )
                        for idx, h in hits.items():
                            all_hits.setdefault(idx, []).extend(h)
                    except Exception as e:
                        st.warning(f"TB lookup failed for {tb_guid}: {e}")
                tb_terms = all_hits if all_hits else {}
        elif "terminology" in enabled and not (memoq_client and tb_guids):
            with col_info:
                st.info("Terminology check skipped — no memoQ TB connected.")

        # Run checks
        engine = QAEngine(enabled_checks=enabled)
        with st.spinner("Running QA checks..."):
            issues = engine.run_all_checks(seg_list, tb_terms=tb_terms)
        st.session_state.qa_issues = issues

    # ── Display results ────────────────────────────────────────────────────
    issues = st.session_state.get("qa_issues", [])

    if not issues and st.session_state.get("qa_issues") is not None and run_qa:
        st.success("\u2705 No QA issues found!")
        return

    if not issues:
        return

    # Summary bar
    errors   = sum(1 for i in issues if i.severity == "error")
    warnings = sum(1 for i in issues if i.severity == "warning")
    st.markdown(
        f"**Results:** {len(issues)} issue(s) — "
        f"\U0001f534 {errors} error(s) &nbsp;|&nbsp; \U0001f7e1 {warnings} warning(s)"
    )
    st.markdown("*Edit the Target column to correct segments, then click **Apply Corrections**.*")

    # Group by check type for display order
    type_order = {k: i for i, k in enumerate(QAEngine.ALL_CHECKS)}
    sorted_issues = sorted(issues, key=lambda x: (type_order.get(x.check_type, 99), x.segment_index))

    # Editable table — one row per issue
    header_cols = st.columns([1, 2, 1, 3, 3, 4])
    for col, hdr in zip(header_cols, ["Row", "Check", "Severity", "Message", "Source", "Target (editable)"]):
        col.markdown(f"**{hdr}**")
    st.markdown("---")

    for iss_idx, iss in enumerate(sorted_issues):
        row_cols = st.columns([1, 2, 1, 3, 3, 4])
        sev_icon = "\U0001f534" if iss.severity == "error" else "\U0001f7e1"
        row_cols[0].write(str(iss.segment_index + 1))
        row_cols[1].write(iss.check_type)
        row_cols[2].write(sev_icon)
        row_cols[3].write(iss.message)
        row_cols[4].write(iss.source_text[:80] + ("\u2026" if len(iss.source_text) > 80 else ""))

        edit_key = f"qa_edit_{iss.segment_id}_{iss_idx}"
        current_val = st.session_state.translation_results.get(iss.segment_id, iss.target_text)
        row_cols[5].text_input(
            label="target",
            value=current_val,
            key=edit_key,
            label_visibility="collapsed",
        )

    st.markdown("---")

    # ── Apply Corrections ─────────────────────────────────────────────────
    if st.button("\u2705 Apply Corrections to XLIFF", type="primary"):
        applied = 0
        for iss_idx, iss in enumerate(sorted_issues):
            edit_key = f"qa_edit_{iss.segment_id}_{iss_idx}"
            new_val = st.session_state.get(edit_key, "").strip()
            old_val = (st.session_state.translation_results.get(iss.segment_id) or "").strip()
            if new_val and new_val != old_val:
                st.session_state.translation_results[iss.segment_id] = new_val
                applied += 1

        if applied:
            st.success(f"\u2705 {applied} correction(s) applied to translation results.")

            xliff_bytes    = st.session_state.get("xliff_bytes")
            xliff_filename = st.session_state.get("xliff_filename", "corrected.xliff")
            segment_objects = st.session_state.get("segment_objects", {})
            match_scores    = st.session_state.get("segment_match_scores", {})

            if xliff_bytes:
                from datetime import datetime
                corrected_xml = XMLParser.update_xliff(
                    xliff_bytes,
                    st.session_state.translation_results,
                    segment_objects,
                    match_scores=match_scores,
                )
                base = xliff_filename.rsplit(".", 1)[0]
                ext  = xliff_filename.rsplit(".", 1)[1] if "." in xliff_filename else "xliff"
                ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
                out_name = f"{base}_qa_corrected_{ts}.{ext}"
                st.download_button(
                    "\u2b07\ufe0f Download QA-Corrected XLIFF",
                    corrected_xml,
                    file_name=out_name,
                    mime="application/xml",
                )
            else:
                st.info("Re-upload the original XLIFF in the Workspace tab to enable download.")
        else:
            st.info("No changes detected. Edit the Target fields above before applying.")

