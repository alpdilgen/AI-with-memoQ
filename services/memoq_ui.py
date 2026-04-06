# services/memoq_ui.py
"""
Streamlit UI Components for memoQ Server Integration
Provides UI for connecting to memoQ Server and selecting TMs/TBs
"""

import streamlit as st
from typing import Dict, List, Tuple, Optional
from services.memoq_server_client import MemoQServerClient
import logging

logger = logging.getLogger(__name__)

class MemoQUI:
    """Streamlit UI for memoQ Server Integration"""
    
    @staticmethod
    def show_connection_settings() -> Optional[MemoQServerClient]:
        """
        Show memoQ Server connection settings in sidebar
        
        Returns:
            MemoQServerClient instance if connected, None otherwise
        """
        with st.sidebar:
            st.divider()
            st.subheader("🔗 memoQ Server Connection")
            
            # Initialize session state for memoQ settings
            if 'memoq_server_url' not in st.session_state:
                st.session_state.memoq_server_url = "https://mirage.memoq.com:9091/adaturkey"
            if 'memoq_username' not in st.session_state:
                st.session_state.memoq_username = "AnovaAI"
            if 'memoq_password' not in st.session_state:
                st.session_state.memoq_password = ""
            if 'memoq_verify_ssl' not in st.session_state:
                st.session_state.memoq_verify_ssl = False
            
            # Connection form
            with st.form("memoq_connection"):
                server_url = st.text_input(
                    "Server URL",
                    value=st.session_state.memoq_server_url,
                    help="memoQ Server base URL"
                )
                
                username = st.text_input(
                    "Username",
                    value=st.session_state.memoq_username,
                    help="memoQ server username"
                )
                
                password = st.text_input(
                    "Password",
                    type="password",
                    value=st.session_state.memoq_password,
                    help="memoQ server password"
                )
                
                verify_ssl = st.checkbox(
                    "Verify SSL",
                    value=st.session_state.memoq_verify_ssl,
                    help="Check for production, uncheck for self-signed certificates"
                )
                
                submitted = st.form_submit_button("🔐 Connect", use_container_width=True)
                
                if submitted:
                    st.session_state.memoq_server_url = server_url
                    st.session_state.memoq_username = username
                    st.session_state.memoq_password = password
                    st.session_state.memoq_verify_ssl = verify_ssl
                    st.session_state.memoq_connected = False
                    st.session_state.memoq_client = None
            
            # Connection status
            if 'memoq_connected' in st.session_state and st.session_state.memoq_connected:
                st.success("✓ Connected to memoQ Server")
                if st.button("🔌 Disconnect", use_container_width=True):
                    st.session_state.memoq_connected = False
                    st.session_state.memoq_client = None
                    st.rerun()
                
                return st.session_state.memoq_client
            
            elif 'memoq_client' in st.session_state and st.session_state.memoq_client:
                # Try to use existing connection
                try:
                    st.session_state.memoq_connected = True
                    st.success("✓ Connected to memoQ Server")
                    return st.session_state.memoq_client
                except Exception as e:
                    st.session_state.memoq_connected = False
                    st.session_state.memoq_client = None
                    st.error(f"⚠️ Connection lost: {e}")
                    return None
            
            return None
    
    @staticmethod
    def show_memoq_data_loader(
        client: MemoQServerClient,
        src_lang: str,
        tgt_lang: str
    ) -> Tuple[List[str], List[str]]:
        """
        Show memoQ Data Loader UI with searchable dropdown lists
        
        Args:
            client: MemoQServerClient instance
            src_lang: Source language code
            tgt_lang: Target language code
        
        Returns:
            Tuple of (selected_tm_guids, selected_tb_guids)
        """
        # Initialize session state
        if 'memoq_tms_loaded' not in st.session_state:
            st.session_state.memoq_tms_loaded = False
            st.session_state.memoq_tbs_loaded = False
            st.session_state.memoq_tms_list = []
            st.session_state.memoq_tbs_list = []
            st.session_state.selected_tm_names = []
            st.session_state.selected_tb_names = []
            st.session_state.tm_search_filter = ""
            st.session_state.tb_search_filter = ""
        
        col1, col2 = st.columns([1, 4])
        
        with col1:
            load_button = st.button(
                "📥 Load",
                use_container_width=True,
                help="Load Translation Memories and Termbases from memoQ Server"
            )
        
        with col2:
            if st.session_state.memoq_tms_loaded:
                st.success(
                    f"✓ Loaded {len(st.session_state.memoq_tms_list)} TM(s) and "
                    f"{len(st.session_state.memoq_tbs_list)} TB(s)"
                )
        
        # Load data when button is pressed
        if load_button:
            with st.spinner("Loading memoQ data..."):
                try:
                    # Convert language codes for memoQ API
                    memoq_src = MemoQUI._get_memoq_lang_code(src_lang)
                    memoq_tgt = MemoQUI._get_memoq_lang_code(tgt_lang)
                    
                    # Load TMs
                    tms = client.list_tms(src_lang=memoq_src, tgt_lang=memoq_tgt)
                    st.session_state.memoq_tms_list = tms
                    
                    # Load TBs
                    tbs = client.list_tbs(languages=[memoq_src, memoq_tgt])
                    st.session_state.memoq_tbs_list = tbs
                    
                    st.session_state.memoq_tms_loaded = True
                    st.session_state.memoq_tbs_loaded = True
                    st.rerun()
                    
                except Exception as e:
                    st.error(f"Failed to load memoQ data: {str(e)}")
                    logger.error(f"memoQ data load error: {e}")
        
        # Display TMs and TBs with searchable dropdowns
        if st.session_state.memoq_tms_loaded and st.session_state.memoq_tbs_loaded:
            st.divider()
            
            col1, col2 = st.columns(2)
            
            # ==================== TRANSLATION MEMORIES ====================
            with col1:
                st.markdown("**📚 Translation Memories**")
                
                if st.session_state.memoq_tms_list:
                    # Create full names with metadata
                    tm_options = {}
                    for tm in st.session_state.memoq_tms_list:
                        display_name = f"{tm['FriendlyName']} ({tm['SourceLangCode'].upper()}-{tm['TargetLangCode'].upper()}, {tm.get('NumEntries', 0)} entries)"
                        tm_options[display_name] = tm["TMGuid"]
                    
                    # Search filter
                    tm_search = st.text_input(
                        "Search",
                        value=st.session_state.tm_search_filter,
                        placeholder="Type to filter TMs...",
                        key="tm_search_input",
                        label_visibility="collapsed"
                    )
                    st.session_state.tm_search_filter = tm_search
                    
                    # Filter options based on search
                    filtered_tm_options = [
                        name for name in tm_options.keys()
                        if tm_search.lower() in name.lower()
                    ]
                    
                    # Multi-select dropdown
                    selected_tm_names = st.multiselect(
                        "Select TMs",
                        options=filtered_tm_options,
                        default=st.session_state.selected_tm_names,
                        key="tm_multiselect",
                        label_visibility="collapsed"
                    )
                    
                    st.session_state.selected_tm_names = selected_tm_names
                    selected_tm_guids = [tm_options[name] for name in selected_tm_names]
                    
                else:
                    st.info("No TMs found")
                    selected_tm_guids = []
            
            # ==================== TERMBASES ====================
            with col2:
                st.markdown("**📖 Termbases**")
                
                if st.session_state.memoq_tbs_list:
                    # Create full names with metadata
                    tb_options = {}
                    for tb in st.session_state.memoq_tbs_list:
                        display_name = f"{tb['FriendlyName']} ({', '.join(tb.get('Languages', []))}, {tb.get('NumEntries', 0)} terms)"
                        tb_options[display_name] = tb["TBGuid"]
                    
                    # Search filter
                    tb_search = st.text_input(
                        "Search",
                        value=st.session_state.tb_search_filter,
                        placeholder="Type to filter TBs...",
                        key="tb_search_input",
                        label_visibility="collapsed"
                    )
                    st.session_state.tb_search_filter = tb_search
                    
                    # Filter options based on search
                    filtered_tb_options = [
                        name for name in tb_options.keys()
                        if tb_search.lower() in name.lower()
                    ]
                    
                    # Multi-select dropdown
                    selected_tb_names = st.multiselect(
                        "Select TBs",
                        options=filtered_tb_options,
                        default=st.session_state.selected_tb_names,
                        key="tb_multiselect",
                        label_visibility="collapsed"
                    )
                    
                    st.session_state.selected_tb_names = selected_tb_names
                    selected_tb_guids = [tb_options[name] for name in selected_tb_names]
                    
                else:
                    st.info("No TBs found")
                    selected_tb_guids = []
            
            # Display summary
            st.divider()
            col1, col2, col3 = st.columns(3)
            
            with col1:
                st.metric("Selected TMs", len(selected_tm_guids))
            
            with col2:
                st.metric("Selected TBs", len(selected_tb_guids))
            
            with col3:
                if selected_tm_guids or selected_tb_guids:
                    st.success(f"✓ Ready: {len(selected_tm_guids) + len(selected_tb_guids)} resources")
        
        else:
            selected_tm_guids = []
            selected_tb_guids = []
        
        return selected_tm_guids, selected_tb_guids
    
    @staticmethod
    def _get_memoq_lang_code(lang_code: str) -> str:
        """
        Language codes are already in memoQ format (3-letter + optional variant)
        
        Args:
            lang_code: memoQ language code (e.g., 'eng', 'eng-GB', 'tur')
        
        Returns:
            Same code (already in correct format)
        """
        # Codes are already memoQ 3-letter codes from config
        return lang_code
    
    @staticmethod
    def show_tm_lookup_results(
        client: MemoQServerClient,
        tm_guids: List[str],
        source_text: str,
        min_match_rate: int = 75,
        source_lang: Optional[str] = None,
        target_lang: Optional[str] = None,
    ) -> Optional[Dict]:
        """
        Show TM lookup results
        
        Args:
            client: MemoQServerClient instance
            tm_guids: List of selected TM GUIDs
            source_text: Source text to lookup
            min_match_rate: Minimum match rate to display
        
        Returns:
            Best match or None
        """
        if not tm_guids or not source_text:
            return None
        
        best_match = None
        best_rate = 0
        
        with st.spinner("🔍 Searching Translation Memories..."):
            for tm_guid in tm_guids:
                try:
                    results = client.lookup_segments(
                        tm_guid,
                        [source_text],
                        source_lang=source_lang,
                        target_lang=target_lang,
                    )
                    hits = results.get("Result", [{}])[0].get("TMHits", [])
                    
                    for hit in hits:
                        match_rate = hit.get("MatchRate", 0)
                        if match_rate >= min_match_rate and match_rate > best_rate:
                            best_match = hit
                            best_rate = match_rate
                
                except Exception as e:
                    logger.warning(f"TM lookup error for {tm_guid}: {e}")
        
        if best_match:
            with st.expander(f"✓ TM Match ({best_match['MatchRate']}%)", expanded=True):
                col1, col2 = st.columns(2)
                
                with col1:
                    st.markdown("**Source (from TM):**")
                    source_seg = best_match["TransUnit"]["SourceSegment"]
                    st.code(source_seg.replace("<seg>", "").replace("</seg>", ""))
                
                with col2:
                    st.markdown("**Target (from TM):**")
                    target_seg = best_match["TransUnit"]["TargetSegment"]
                    st.code(target_seg.replace("<seg>", "").replace("</seg>", ""))
                
                # Show metadata
                st.divider()
                meta = best_match["TransUnit"]
                col1, col2, col3 = st.columns(3)
                
                with col1:
                    st.caption(f"📂 Domain: {meta.get('Domain', 'N/A')}")
                with col2:
                    st.caption(f"👤 Creator: {meta.get('Creator', 'N/A')}")
                with col3:
                    st.caption(f"📅 Created: {meta.get('Created', 'N/A')[:10]}")
        
        return best_match
