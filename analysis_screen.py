"""
ANALYSIS SCREEN - Pre-translation analysis using memoQ Server TMs
Simple addition to existing workflow
"""

import streamlit as st
import pandas as pd


def calculate_cost_estimate(analysis_results, model="gpt-4o"):
    """Calculate estimated cost based on analysis"""
    # Approximate: 1 word ≈ 1.5 tokens (conservative estimate)
    tokens_per_word = 1.5
    total_words = analysis_results.get('total_words', 0)
    total_segments = analysis_results.get('total_segments', 1)
    avg_tokens_per_segment = max(int((total_words / max(total_segments, 1)) * tokens_per_word), 50)

    if model == "gpt-4o":
        input_price = 0.00025
        output_price = 0.001
    else:
        input_price = 0.00025
        output_price = 0.001

    cost_per_segment = (avg_tokens_per_segment * input_price) + (avg_tokens_per_segment * output_price)
    
    # 101% ve 100% = bypass (no cost)
    bypass_segs = (
        analysis_results['by_level'].get('101% (Context)', {}).get('segments', 0) +
        analysis_results['by_level'].get('100%', {}).get('segments', 0)
    )

    # 95%-99%, 85%-94%, 75%-84%, 50%-74% = context cost (50% discount)
    context_segs = sum(
        analysis_results['by_level'].get(level, {}).get('segments', 0)
        for level in ['95%-99%', '85%-94%', '75%-84%', '50%-74%']
    )

    # < 50% = full LLM cost
    llm_segs = analysis_results['total_segments'] - bypass_segs - context_segs
    
    bypass_cost = 0
    context_cost = context_segs * cost_per_segment * 0.5
    llm_cost = llm_segs * cost_per_segment
    total_cost = bypass_cost + context_cost + llm_cost
    
    return {
        'bypass_cost': bypass_cost,
        'context_cost': context_cost,
        'llm_cost': llm_cost,
        'total_cost': total_cost,
        'breakdown': {
            'bypass': bypass_segs,
            'context': context_segs,
            'llm_only': llm_segs
        }
    }


def show_analysis_screen(analysis_results):
    """Show analysis screen before translation - display results only"""

    # Display analysis results
    cost = calculate_cost_estimate(analysis_results)

    st.markdown("### Results")

    # Metrics
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("📊 Segments", analysis_results['total_segments'])
    with col2:
        st.metric("📝 Words", analysis_results['total_words'])
    with col3:
        tm_cov = (cost['breakdown']['bypass'] + cost['breakdown']['context']) / analysis_results['total_segments'] * 100
        st.metric("🎯 TM Coverage", f"{tm_cov:.1f}%")
    with col4:
        st.metric("💰 Est. Cost", f"${cost['total_cost']:.4f}")

    st.markdown("---")
    st.markdown("#### Match Distribution")

    breakdown_data = []
    for level in ['101% (Context)', '100%', '95%-99%', '85%-94%', '75%-84%', '50%-74%', 'No match']:
        if level in analysis_results['by_level']:
            data = analysis_results['by_level'][level]
            pct = (data['words'] / analysis_results['total_words'] * 100) if analysis_results['total_words'] > 0 else 0
            breakdown_data.append({
                'Match Level': level,
                'Segments': data['segments'],
                'Words': data['words'],
                'Percentage': f"{pct:.1f}%"
            })

    df = pd.DataFrame(breakdown_data)
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.markdown("---")
    st.markdown("#### Cost Breakdown")

    cost_data = [
        {'Category': 'Bypass (≥95%)', 'Segments': cost['breakdown']['bypass'], 'Cost': f"${cost['bypass_cost']:.4f}"},
        {'Category': 'Context (60-94%)', 'Segments': cost['breakdown']['context'], 'Cost': f"${cost['context_cost']:.4f}"},
        {'Category': 'LLM Only (<60%)', 'Segments': cost['breakdown']['llm_only'], 'Cost': f"${cost['llm_cost']:.4f}"}
    ]
    cost_df = pd.DataFrame(cost_data)
    st.dataframe(cost_df, use_container_width=True, hide_index=True)

    st.markdown("---")

    # Action buttons
    col1, col2 = st.columns(2)
    with col1:
        if st.button("✅ Proceed to Translation", type="primary", use_container_width=True):
            st.session_state.ready_to_translate = True
            st.session_state.analysis_triggered = False
            st.rerun()

    with col2:
        if st.button("🔄 Clear Analysis", use_container_width=True):
            st.session_state.analysis_triggered = False
            st.session_state.analysis_results = None
            st.rerun()
