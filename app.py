import streamlit as st
import pandas as pd
import time
import re
from datetime import datetime
from openai import AuthenticationError
from services.tm_matcher import TMatcher
from services.tb_matcher import TBMatcher
from services.prompt_builder import PromptBuilder
from services.ai_translator import AITranslator
from services.caching import CacheManager
from services.doc_analyzer import DocumentAnalyzer, PromptGenerator
from services.embedding_matcher import EmbeddingMatcher, get_embedding_cost_estimate
from utils.xml_parser import XMLParser
from utils.logger import TransactionLogger
import config
from services.memoq_server_client import MemoQServerClient
from services.memoq_ui import MemoQUI
from analysis_screen import show_analysis_screen
# --- Setup ---
st.set_page_config(page_title=config.APP_NAME, layout="wide", page_icon="🌍")

# Session state initialization
if 'translation_results' not in st.session_state:
    st.session_state.translation_results = {}
if 'segment_objects' not in st.session_state:
    st.session_state.segment_objects = {}
if 'translation_log' not in st.session_state:
    st.session_state.translation_log = ""
if 'tm_info' not in st.session_state:
    st.session_state.tm_info = None
if 'bypass_stats' not in st.session_state:
    st.session_state.bypass_stats = {'bypassed': 0, 'llm_sent': 0}
if 'detected_languages' not in st.session_state:
    st.session_state.detected_languages = {'source': None, 'target': None}
if 'chat_history' not in st.session_state:
    st.session_state.chat_history = []
# Prompt Builder state
if 'generated_prompt' not in st.session_state:
    st.session_state.generated_prompt = None
if 'prompt_metadata' not in st.session_state:
    st.session_state.prompt_metadata = {}
if 'use_generated_prompt' not in st.session_state:
    st.session_state.use_generated_prompt = False
# Reference file state
if 'reference_chunks' not in st.session_state:
    st.session_state.reference_chunks = []
if 'embedding_matcher' not in st.session_state:
    st.session_state.embedding_matcher = None
if 'reference_embeddings_ready' not in st.session_state:
    st.session_state.reference_embeddings_ready = False
# DNT (Do Not Translate) list
if 'dnt_terms' not in st.session_state:
    st.session_state.dnt_terms = []

# memoQ Server state
if 'memoq_server_url' not in st.session_state:
    st.session_state.memoq_server_url = "https://mirage.memoq.com:9091/adaturkey"
if 'memoq_username' not in st.session_state:
    st.session_state.memoq_username = ""
if 'memoq_password' not in st.session_state:
    st.session_state.memoq_password = ""
if 'memoq_verify_ssl' not in st.session_state:
    st.session_state.memoq_verify_ssl = False
if 'memoq_connected' not in st.session_state:
    st.session_state.memoq_connected = False
if 'memoq_client' not in st.session_state:
    st.session_state.memoq_client = None
if 'selected_tm_guids' not in st.session_state:
    st.session_state.selected_tm_guids = []
if 'selected_tb_guids' not in st.session_state:
    st.session_state.selected_tb_guids = []
if 'memoq_tms_list' not in st.session_state:
    st.session_state.memoq_tms_list = []
if 'memoq_tbs_list' not in st.session_state:
    st.session_state.memoq_tbs_list = []

if 'analysis_triggered' not in st.session_state:
    st.session_state.analysis_triggered = False
if 'ready_to_translate' not in st.session_state:
    st.session_state.ready_to_translate = False
if 'batch_size' not in st.session_state:
    st.session_state.batch_size = 20
if 'analysis_results' not in st.session_state:
    st.session_state.analysis_results = None
if 'segment_match_scores' not in st.session_state:
    st.session_state.segment_match_scores = {}

# --- Sidebar ---
with st.sidebar:
    st.title("⚙️ Configuration")
    
    # Language Settings
    st.subheader("🌐 Languages")
    
    detected_src = st.session_state.detected_languages.get('source')
    detected_tgt = st.session_state.detected_languages.get('target')
    
    # Convert detected ISO codes (en-gb) to memoQ codes (eng-GB)
    detected_src = config.convert_detected_lang(detected_src) if detected_src else None
    detected_tgt = config.convert_detected_lang(detected_tgt) if detected_tgt else None
    
    lang_keys = list(config.SUPPORTED_LANGUAGES.keys())
    
    src_default = lang_keys.index(detected_src) if detected_src in lang_keys else lang_keys.index('eng')
    tgt_default = lang_keys.index(detected_tgt) if detected_tgt in lang_keys else lang_keys.index('tur')
    
    src_code = st.selectbox(
        "Source Language", 
        lang_keys, 
        index=src_default,
        format_func=lambda x: f"{config.SUPPORTED_LANGUAGES[x]} ({x})" + (" ✓" if x == detected_src else "")
    )
    tgt_code = st.selectbox(
        "Target Language", 
        lang_keys, 
        index=tgt_default,
        format_func=lambda x: f"{config.SUPPORTED_LANGUAGES[x]} ({x})" + (" ✓" if x == detected_tgt else "")
    )
    
    if detected_src and detected_tgt:
        st.caption(f"🔍 Auto-detected: {detected_src} → {detected_tgt}")
    
    st.divider()
    
    # AI Settings
    st.subheader("🤖 AI Settings")
    api_key = st.text_input("API Key", type="password")
    model = st.selectbox("Model", config.OPENAI_MODELS)
    
    st.divider()
    
    # TM Settings
    st.subheader("📚 TM Settings")
    
    acceptance_threshold = st.slider(
        "TM Acceptance Threshold",
        min_value=70,
        max_value=100,
        value=config.DEFAULT_ACCEPTANCE_THRESHOLD,
        help="Matches ≥ this value bypass LLM (direct TM usage)"
    )
    
    match_threshold = st.slider(
        "TM Match Threshold",
        min_value=50,
        max_value=100,
        value=config.DEFAULT_MATCH_THRESHOLD,
        help="Matches ≥ this value are sent as context to LLM"
    )
    
    if acceptance_threshold <= match_threshold:
        st.warning("Acceptance should be higher than Match threshold")
    
    st.divider()
    
    # Chat History Settings
    st.subheader("💬 Chat History")
    chat_history_length = st.slider(
        "Previous batches to include",
        min_value=0,
        max_value=10,
        value=config.DEFAULT_CHAT_HISTORY,
        help="Number of previous translation batches to include for consistency"
    )
    
    st.divider()
    
    # Batch Size Settings
    st.subheader("📦 Batch Processing")
    batch_size = st.slider(
        "Batch Size",
        min_value=5,
        max_value=50,
        value=st.session_state.batch_size,
        step=5,
        help="Number of segments per batch sent to LLM"
    )
    st.session_state.batch_size = batch_size
    
    st.divider()
    
    # Cache Management
    st.subheader("🗄️ TM Cache")
    cache_files = CacheManager.get_cache_info()
    if cache_files:
        st.caption(f"{len(cache_files)} cached TM(s)")
        if st.button("🗑️ Clear All Cache", type="secondary", width="stretch"):
            count = CacheManager.clear_tm_cache()
            st.success(f"Cleared {count} cache file(s)")
            st.rerun()
    else:
        st.caption("No cached TMs")

# ==================== memoQ SERVER CONNECTION ====================
    st.divider()
    st.subheader("🔗 memoQ Server")
    
    with st.form("memoq_connection_form"):
        memoq_url = st.text_input(
            "Server URL",
            value=st.session_state.memoq_server_url,
            help="memoQ Server base URL",
            key="memoq_url_input"
        )
        
        memoq_user = st.text_input(
            "Username",
            value=st.session_state.memoq_username,
            key="memoq_user_input"
        )
        
        memoq_pass = st.text_input(
            "Password",
            type="password",
            value=st.session_state.memoq_password,
            key="memoq_pass_input"
        )
        
        memoq_ssl = st.checkbox(
            "Verify SSL",
            value=st.session_state.memoq_verify_ssl,
            help="Disable for self-signed certificates"
        )
        
        memoq_connect = st.form_submit_button("🔐 Connect", width="stretch")
    
    if memoq_connect:
        st.session_state.memoq_server_url = memoq_url
        st.session_state.memoq_username = memoq_user
        st.session_state.memoq_password = memoq_pass
        st.session_state.memoq_verify_ssl = memoq_ssl
        
        try:
            client = MemoQServerClient(
                server_url=memoq_url,
                username=memoq_user,
                password=memoq_pass,
                verify_ssl=memoq_ssl
            )
            client.login()
            st.session_state.memoq_client = client
            st.session_state.memoq_connected = True
            st.success("✓ Connected to memoQ Server")
        except Exception as e:
            st.error(f"Connection failed: {str(e)}")
            st.session_state.memoq_connected = False
            st.session_state.memoq_client = None
    
    if st.session_state.memoq_connected and st.session_state.memoq_client:
        st.success("✓ Connected to memoQ Server")
        if st.button("🔌 Disconnect", width="stretch"):
            st.session_state.memoq_connected = False
            st.session_state.memoq_client = None
            st.rerun()
    
    # Show if using generated prompt
    if st.session_state.use_generated_prompt and st.session_state.generated_prompt:
        st.divider()
        st.success("✨ Using generated prompt")
        if st.button("❌ Clear Generated Prompt"):
            st.session_state.use_generated_prompt = False
            st.session_state.generated_prompt = None
            st.rerun()


# --- Helper Functions ---

def parse_reference_file(content: bytes, filename: str) -> list:
    """
    Parse reference file (target-only text) into chunks for style reference.
    Supports TXT, DOCX, PDF, HTML, RTF, and Excel formats.
    
    Returns list of text chunks (sentences/paragraphs).
    """
    chunks = []
    filename_lower = filename.lower()
    
    try:
        # === TXT ===
        if filename_lower.endswith('.txt'):
            text = None
            for encoding in ['utf-8', 'utf-8-sig', 'utf-16', 'latin-1', 'cp1252', 'iso-8859-9']:
                try:
                    text = content.decode(encoding)
                    break
                except Exception:
                    continue
            
            if text:
                text = text.replace('\r\n', '\n').replace('\r', '\n')
                lines = [line.strip() for line in text.split('\n') if line.strip()]
                
                import re
                for line in lines:
                    clean_line = re.sub(r'\d+$', '', line).strip()
                    if clean_line and len(clean_line) > 15:
                        chunks.append(clean_line)
        
        # === DOCX ===
        elif filename_lower.endswith('.docx'):
            from docx import Document
            import io
            doc = Document(io.BytesIO(content))
            for para in doc.paragraphs:
                text = para.text.strip()
                if text and len(text) > 15:
                    chunks.append(text)
        
        # === PDF ===
        elif filename_lower.endswith('.pdf'):
            import io
            try:
                import pdfplumber
                with pdfplumber.open(io.BytesIO(content)) as pdf:
                    for page in pdf.pages:
                        text = page.extract_text()
                        if text:
                            lines = [line.strip() for line in text.split('\n') if line.strip()]
                            for line in lines:
                                if len(line) > 15:
                                    chunks.append(line)
            except ImportError:
                st.warning("PDF support requires pdfplumber: pip install pdfplumber")
        
        # === HTML ===
        elif filename_lower.endswith(('.html', '.htm')):
            try:
                from bs4 import BeautifulSoup
                text = None
                for encoding in ['utf-8', 'utf-8-sig', 'latin-1', 'cp1252']:
                    try:
                        text = content.decode(encoding)
                        break
                    except Exception:
                        continue
                
                if text:
                    soup = BeautifulSoup(text, 'html.parser')
                    # Remove script and style elements
                    for element in soup(['script', 'style', 'head', 'meta', 'link']):
                        element.decompose()
                    
                    # Get text from paragraphs, divs, list items, etc.
                    for tag in soup.find_all(['p', 'div', 'li', 'td', 'th', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
                        text = tag.get_text(strip=True)
                        if text and len(text) > 15:
                            chunks.append(text)
            except ImportError:
                st.warning("HTML support requires beautifulsoup4: pip install beautifulsoup4")
        
        # === RTF ===
        elif filename_lower.endswith('.rtf'):
            try:
                from striprtf.striprtf import rtf_to_text
                text = rtf_to_text(content.decode('latin-1', errors='ignore'))
                if text:
                    text = text.replace('\r\n', '\n').replace('\r', '\n')
                    lines = [line.strip() for line in text.split('\n') if line.strip()]
                    for line in lines:
                        if len(line) > 15:
                            chunks.append(line)
            except ImportError:
                st.warning("RTF support requires striprtf: pip install striprtf")
        
        # === Excel (XLSX, XLS) ===
        elif filename_lower.endswith(('.xlsx', '.xls')):
            import io
            try:
                df = pd.read_excel(io.BytesIO(content), header=None)
                # Iterate through all cells
                for col in df.columns:
                    for value in df[col]:
                        if pd.notna(value):
                            text = str(value).strip()
                            if text and len(text) > 15:
                                # Skip if it's just a number
                                try:
                                    float(text.replace(',', '.'))
                                    continue
                                except Exception:
                                    chunks.append(text)
            except Exception as e:
                st.warning(f"Excel parsing error: {e}")
                    
    except Exception as e:
        st.warning(f"Error parsing reference file: {e}")
    
    # Remove duplicates while preserving order
    seen = set()
    unique_chunks = []
    for chunk in chunks:
        if chunk not in seen:
            seen.add(chunk)
            unique_chunks.append(chunk)
    
    return unique_chunks


def get_reference_samples(chunks: list, batch_num: int, samples_per_batch: int = 5, max_chars: int = 1500) -> str:
    """
    Get reference samples for a batch using rotating selection.
    
    Args:
        chunks: List of reference text chunks
        batch_num: Current batch number (for rotation)
        samples_per_batch: How many samples to include
        max_chars: Maximum total characters for all samples
        
    Returns:
        Formatted string of reference samples
    """
    if not chunks:
        return ""
    
    # Rotating selection - different chunks for each batch
    total_chunks = len(chunks)
    start_idx = (batch_num * samples_per_batch) % total_chunks
    
    selected = []
    total_len = 0
    
    for i in range(samples_per_batch):
        idx = (start_idx + i) % total_chunks
        chunk = chunks[idx]
        
        # Truncate long chunks
        if len(chunk) > 300:
            chunk = chunk[:300] + "..."
        
        if total_len + len(chunk) > max_chars:
            break
            
        selected.append(chunk)
        total_len += len(chunk)
    
    if not selected:
        return ""
    
    return "\n".join(f"• {s}" for s in selected)


def parse_dnt_file(content: bytes, filename: str) -> list:
    """
    Parse Do Not Translate / Forbidden Terms file.
    Supports TXT and CSV formats.
    
    Returns list of terms that should not be translated.
    """
    terms = []
    filename_lower = filename.lower()
    
    try:
        if filename_lower.endswith('.txt'):
            text = None
            for encoding in ['utf-8', 'utf-8-sig', 'latin-1', 'cp1252']:
                try:
                    text = content.decode(encoding)
                    break
                except Exception:
                    continue
            
            if text:
                for line in text.split('\n'):
                    line = line.strip()
                    # Skip empty lines and comments
                    if line and not line.startswith('#'):
                        terms.append(line)
        
        elif filename_lower.endswith('.csv'):
            text = None
            for encoding in ['utf-8', 'utf-8-sig', 'latin-1', 'cp1252']:
                try:
                    text = content.decode(encoding)
                    break
                except Exception:
                    continue
            
            if text:
                for line in text.split('\n'):
                    line = line.strip()
                    if line and not line.startswith('#'):
                        # Take first column
                        parts = line.split(',')
                        term = parts[0].strip().strip('"').strip("'")
                        # Skip header-like entries
                        if term.lower() not in ['term', 'forbidden', 'dnt', 'do not translate', 'source']:
                            if term:
                                terms.append(term)
    
    except Exception as e:
        st.warning(f"Error parsing DNT file: {e}")
    
    # Remove duplicates while preserving order
    seen = set()
    unique_terms = []
    for term in terms:
        if term not in seen:
            seen.add(term)
            unique_terms.append(term)
    
    return unique_terms


def apply_tm_to_segment(source_with_tags: str, tm_translation: str) -> str:
    """Apply TM translation while preserving source tags ({{N}}, <ph>, <bpt>, <ept>, etc.)."""
    import re

    # Detect ALL tag types: {{N}}, <ph>, <bpt>, <ept>, etc.
    all_tags = re.findall(r'(\{\{\d+\}\}|<[a-z]+[^>]*/?[^>]*/?>)', source_with_tags)

    if not all_tags:
        return tm_translation

    # Check if tags already in translation
    if '{{' in tm_translation or '<' in tm_translation:
        return tm_translation

    # Extract leading tags (beginning of source)
    leading_match = re.match(r'^((?:\{\{\d+\}\}|<[a-z]+[^>]*/?[^>]*/?>))+', source_with_tags)
    if leading_match:
        leading_tags = leading_match.group()
        if not tm_translation.startswith(leading_tags):
            tm_translation = leading_tags + tm_translation

    # Extract trailing tags (end of source)
    trailing_match = re.search(r'((?:\{\{\d+\}\}|<[a-z]+[^>]*/?[^>]*/?>))+$', source_with_tags)
    if trailing_match:
        trailing_tags = trailing_match.group()
        if not tm_translation.endswith(trailing_tags):
            tm_translation = tm_translation + trailing_tags

    return tm_translation


def get_chat_history_context(history: list, max_items: int) -> list:
    """Get recent translation history for context."""
    if not history or max_items <= 0:
        return []
    return history[-max_items:]


def normalize_segment_for_matching(source_text: str) -> str:
    """Remove XML/inline tags for TM matching"""
    import re

    # Remove all inline tags: <ph>, <bpt>, <ept>, {{N}}, etc.
    normalized = re.sub(r'<[^>]+>', '', source_text)  # XML tags
    normalized = re.sub(r'\{\{[^}]+\}\}', '', normalized)  # {{1}} style tags

    # Clean up extra spaces
    normalized = re.sub(r'\s+', ' ', normalized).strip()

    return normalized


# --- Main Translation Logic ---

def process_translation(xliff_bytes, tmx_bytes, csv_bytes, custom_prompt_content=None, memoq_tm_guids=None, memoq_tb_guids=None):
    start_time = time.time()  # Track total processing duration

    # Initialize match scores tracking for memoQ metadata
    match_scores = {}

    if not api_key:
        st.error("Please provide an API Key.")
        return

    with st.status("Processing...", expanded=True) as status:
        
        # 1. Parse XLIFF
        st.write("📄 Parsing XLIFF...")
        segments = XMLParser.parse_xliff(xliff_bytes)
        total_segments = len(segments)
        st.write(f"✅ Loaded {total_segments} segments")
        
        st.session_state.segment_objects = {seg.id: seg for seg in segments}
        st.session_state.chat_history = []
        
        # Initialize Logger
        logger = TransactionLogger()
        logger.log(f"Started translation job for {total_segments} segments.")
        logger.log(f"Source: {src_code} | Target: {tgt_code} | Model: {model}")
        logger.log(f"TM Acceptance: ≥{acceptance_threshold}% | TM Match: ≥{match_threshold}%")
        logger.log(f"Chat History Length: {chat_history_length}")
        
        if st.session_state.reference_chunks:
            logger.log(f"Reference file: {len(st.session_state.reference_chunks)} style samples loaded")
        
        if st.session_state.use_generated_prompt:
            logger.log("Using generated prompt from Prompt Builder")
        
        # 2. Initialize TM Matcher
        tm_matcher = None
        if tmx_bytes:
            st.write("🔄 Loading Translation Memory...")
            load_start = time.time()
            
            tm_matcher = TMatcher(
                tmx_bytes, 
                src_code, 
                tgt_code, 
                acceptance_threshold=acceptance_threshold
            )
            
            load_time = time.time() - load_start
            
            if load_time < 2:
                st.write(f"✅ TM Ready: {tm_matcher.tu_count:,} TUs (cached, {load_time:.1f}s)")
            else:
                st.write(f"✅ TM Indexed: {tm_matcher.tu_count:,} TUs ({load_time:.1f}s)")
            
            st.session_state.tm_info = {
                'tu_count': tm_matcher.tu_count,
                'load_time': load_time,
                'file_hash': tm_matcher.file_hash
            }
            logger.log(f"TM loaded: {tm_matcher.tu_count} TUs in {load_time:.2f}s")
        
        # 3. Initialize TB Matcher
        tb_matcher = None
        if csv_bytes:
            st.write("🔄 Loading Termbase...")
            tb_matcher = TBMatcher(csv_bytes)
            st.write(f"✅ Termbase Ready: {tb_matcher.term_count:,} terms")
            logger.log(f"Termbase loaded: {tb_matcher.term_count} terms (columns: {tb_matcher.src_col} → {tb_matcher.tgt_col})")
        
        # 3.5 Initialize memoQ Server client if TMs/TBs selected
        memoq_client = None
        if memoq_tm_guids or memoq_tb_guids:
            try:
                if st.session_state.get('memoq_client'):
                    memoq_client = st.session_state.memoq_client
                    st.write(f"🔗 Using memoQ Server TM/TB resources")
                    if memoq_tm_guids:
                        st.write(f"   • {len(memoq_tm_guids)} Translation Memory(ies)")
                    if memoq_tb_guids:
                        st.write(f"   • {len(memoq_tb_guids)} Termbase(s)")
                    logger.log(f"memoQ Server: {len(memoq_tm_guids)} TMs, {len(memoq_tb_guids)} TBs")
            except Exception as e:
                st.warning(f"Could not connect to memoQ Server: {str(e)}")
                logger.log(f"memoQ connection error: {e}")
        
        # 4. Initialize Prompt Builder
        # Priority: Generated prompt > Custom file > Default
        if st.session_state.use_generated_prompt and st.session_state.generated_prompt:
            prompt_builder = PromptBuilder(custom_template=st.session_state.generated_prompt)
            logger.log("Using generated prompt template from Prompt Builder.")
        elif custom_prompt_content:
            prompt_builder = PromptBuilder(custom_template=custom_prompt_content)
            logger.log("Using custom prompt template from file.")
        else:
            prompt_builder = PromptBuilder(template_path=config.PROMPT_TEMPLATE_PATH)
            logger.log("Using default prompt template.")
        
        translator = AITranslator("OpenAI", api_key, model)
        
        status.update(label="Analyzing segments...", state="running")
        
        # 5. Analyze segments
        bypass_segments = []
        llm_segments = []
        final_translations = {}
        tm_context = {}
        tb_context = {}

        st.write("🔍 Analyzing TM matches...")
        analysis_progress = st.progress(0)

        # STEP A: Handle tag-only segments first
        segments_needing_tm = []
        for seg in segments:
            text_only = re.sub(r'<[^>]+>|\{\{\d+\}\}', '', seg.source).strip()
            if not text_only:
                final_translations[seg.id] = seg.source
                match_scores[seg.id] = 100
                bypass_segments.append(seg)
            else:
                segments_needing_tm.append(seg)

        # STEP B: Local TM matching (if available)
        local_tm_matched_ids = set()
        if tm_matcher:
            for i, seg in enumerate(segments_needing_tm):
                should_bypass, tm_translation, match_score = tm_matcher.should_bypass_llm(
                    seg.source, match_threshold=match_threshold
                )
                if should_bypass and tm_translation:
                    bypass_segments.append(seg)
                    final_translations[seg.id] = apply_tm_to_segment(seg.source, tm_translation)
                    match_scores[seg.id] = match_score
                    local_tm_matched_ids.add(seg.id)
                else:
                    matches, _ = tm_matcher.extract_matches(seg.source, threshold=match_threshold)
                    if matches:
                        tm_context[seg.id] = matches
                        match_scores[seg.id] = max(m.similarity for m in matches)
                        local_tm_matched_ids.add(seg.id)
                    # Don't add to llm_segments yet — memoQ might find better match
                analysis_progress.progress((i + 1) / len(segments_needing_tm) * 0.3)

        # STEP C: memoQ Server TM batch lookup (for segments without local TM bypass)
        # Segments that got bypassed by local TM should NOT go to memoQ
        segments_for_memoq = [s for s in segments_needing_tm if s.id not in {s2.id for s2 in bypass_segments}]

        BATCH_SIZE = 50

        if memoq_client and memoq_tm_guids and segments_for_memoq:
            for tm_guid in memoq_tm_guids:
                remaining = [s for s in segments_for_memoq if s.id not in {s2.id for s2 in bypass_segments} or s.id in tm_context]

                for batch_start in range(0, len(remaining), BATCH_SIZE):
                    batch = remaining[batch_start:batch_start + BATCH_SIZE]
                    normalized_sources = [normalize_segment_for_matching(s.source) for s in batch]

                    try:
                        results = memoq_client.lookup_segments(
                            tm_guid, normalized_sources,
                            match_threshold=match_threshold,
                            src_lang=src_code, tgt_lang=tgt_code
                        )

                        if results:
                            for idx, seg in enumerate(batch):
                                if idx in results:
                                    tm_hits = results[idx]
                                    if tm_hits:
                                        best_hit = tm_hits[0]
                                        score = best_hit.similarity

                                        if score >= acceptance_threshold:
                                            bypass_segments.append(seg)
                                            final_translations[seg.id] = best_hit.target_text
                                            match_scores[seg.id] = score
                                        elif score >= match_threshold:
                                            # Only update if memoQ has better match than local TM
                                            existing_score = match_scores.get(seg.id, 0)
                                            if score > existing_score:
                                                tm_context[seg.id] = tm_hits
                                                match_scores[seg.id] = score
                    except Exception as e:
                        logger.log(f"memoQ TM batch lookup error: {e}")

                    progress = 0.3 + (batch_start + len(batch)) / max(len(remaining), 1) * 0.3
                    analysis_progress.progress(min(progress, 0.6))

        # STEP D: Determine which segments go to LLM
        bypassed_ids = {s.id for s in bypass_segments}
        for seg in segments_needing_tm:
            if seg.id not in bypassed_ids:
                llm_segments.append(seg)
                if seg.id not in match_scores:
                    match_scores[seg.id] = 0

        # STEP E: memoQ TB lookup via search endpoint
        # The lookupterms API returns empty TBHits for full segments on this server.
        # Instead, we use POST /tbs/{tbGuid}/search (concordance search) to find
        # TB entries matching n-grams extracted from source segments.
        if memoq_client and memoq_tb_guids:
            import json as _json
            import re as _re
            from models.entities import TermMatch

            all_segment_sources = [s.source for s in segments_needing_tm]
            logger.log(f"TB Lookup: {len(memoq_tb_guids)} TB(s), {len(all_segment_sources)} segments, src={src_code}, tgt={tgt_code}")

            for tb_idx, tb_guid in enumerate(memoq_tb_guids):
                logger.log(f"  TB [{tb_idx+1}/{len(memoq_tb_guids)}]: {tb_guid}")

                # --- Step 1: Extract unique n-grams from all segments ---
                all_ngrams = set()
                segment_words = {}  # seg_index -> cleaned lowercase text
                for seg_i, src in enumerate(all_segment_sources):
                    clean = _re.sub(r'<[^>]+>', '', src)
                    clean = _re.sub(r'\{\{[^}]+\}\}', '', clean)
                    clean = _re.sub(r'\s+', ' ', clean).strip()
                    segment_words[seg_i] = clean.lower()
                    words = clean.split()
                    # Generate 1-5 word n-grams
                    for n in range(1, 6):
                        for i in range(len(words) - n + 1):
                            ngram = ' '.join(words[i:i+n])
                            # Strip punctuation from edges
                            ngram = ngram.strip('.,;:!?()[]{}"\'-—–')
                            if len(ngram) > 3 and not ngram.isdigit():
                                all_ngrams.add(ngram.lower())

                # Prioritize longer n-grams (more likely to be real terms)
                sorted_ngrams = sorted(all_ngrams, key=len, reverse=True)
                # Limit to top 150 to avoid too many API calls
                search_ngrams = sorted_ngrams[:150]
                logger.log(f"  Extracted {len(all_ngrams)} unique n-grams, searching top {len(search_ngrams)}")

                # --- Step 2: Search TB for each n-gram ---
                found_terms = []  # List of TermMatch
                seen_pairs = set()

                for ngram in search_ngrams:
                    try:
                        search_result = memoq_client._make_request(
                            "POST", f"/tbs/{tb_guid}/search",
                            data={"SearchExpression": ngram}
                        )
                        if not search_result:
                            continue

                        # Parse search results — try multiple response formats
                        entries = []
                        if isinstance(search_result, list):
                            entries = search_result
                        elif isinstance(search_result, dict):
                            entries = search_result.get('Result', search_result.get('Entries', search_result.get('results', [])))
                            if not isinstance(entries, list):
                                entries = [entries] if isinstance(entries, dict) else []

                        for entry in entries:
                            if not isinstance(entry, dict):
                                continue
                            # Try to extract source/target terms from entry
                            languages = entry.get('Languages', [])
                            if not isinstance(languages, list):
                                continue

                            src_terms = []
                            tgt_terms = []
                            for lang_entry in languages:
                                if not isinstance(lang_entry, dict):
                                    continue
                                lang_code = lang_entry.get('Language', '').lower()
                                term_items = lang_entry.get('TermItems', [])
                                if not isinstance(term_items, list):
                                    term_items = [term_items] if isinstance(term_items, dict) else []

                                for ti in term_items:
                                    if not isinstance(ti, dict):
                                        continue
                                    txt = ti.get('Text', '').strip()
                                    if not txt or ti.get('IsForbidden', False):
                                        continue
                                    if lang_code.startswith(src_code.lower()[:3]):
                                        src_terms.append(txt)
                                    elif lang_code.startswith(tgt_code.lower()[:3]):
                                        tgt_terms.append(txt)

                            for st in src_terms:
                                for tt in tgt_terms:
                                    pair_key = (st.lower(), tt.lower())
                                    if pair_key not in seen_pairs:
                                        seen_pairs.add(pair_key)
                                        found_terms.append(TermMatch(
                                            source=st, target=tt,
                                            source_language=src_code,
                                            target_language=tgt_code
                                        ))
                    except Exception:
                        continue

                logger.log(f"  TB search found {len(found_terms)} unique term pairs")
                for ft in found_terms[:20]:
                    logger.log(f"    '{ft.source}' → '{ft.target}'")
                if len(found_terms) > 20:
                    logger.log(f"    ... and {len(found_terms) - 20} more")

                # --- Step 3: Match found terms against each segment ---
                if found_terms:
                    for seg_i, seg in enumerate(segments_needing_tm):
                        seg_lower = segment_words.get(seg_i, seg.source.lower())
                        matching_terms = []
                        for ft in found_terms:
                            if ft.source.lower() in seg_lower:
                                matching_terms.append(ft)
                        if matching_terms:
                            existing = tb_context.get(seg.id, [])
                            tb_context[seg.id] = existing + matching_terms

                tb_matched_segs = sum(1 for sid in tb_context if tb_context[sid])
                logger.log(f"  TB total: {len(found_terms)} terms matched across {tb_matched_segs} segments")

            analysis_progress.progress(0.8)

        # Local TB matching
        if tb_matcher:
            for seg in segments_needing_tm:
                tb_matches = tb_matcher.extract_matches(seg.source)
                if tb_matches:
                    existing = tb_context.get(seg.id, [])
                    tb_context[seg.id] = existing + tb_matches

        analysis_progress.progress(1.0)
        
        st.session_state.bypass_stats = {
            'bypassed': len(bypass_segments),
            'llm_sent': len(llm_segments)
        }
        
        st.write(f"✅ **{len(bypass_segments)}** segments from TM (≥{acceptance_threshold}% match)")
        st.write(f"🔄 **{len(llm_segments)}** segments need LLM translation")
        
        logger.log(f"Analysis complete: {len(bypass_segments)} bypass, {len(llm_segments)} LLM")
        logger.log_tm_matches(tm_context)
        logger.log_tb_matches(tb_context)
        
        # IMPROVEMENT: Reorder LLM segments - process WITH context first
        # This builds chat history early for better translation consistency
        segments_with_context = [s for s in llm_segments if s.id in tm_context]
        segments_no_context = [s for s in llm_segments if s.id not in tm_context]
        
        original_count = len(llm_segments)
        llm_segments = segments_with_context + segments_no_context
        
        logger.log(f"Segments reordered: {len(segments_with_context)} with TM context, {len(segments_no_context)} without")
        logger.log(f"Processing order optimized for better consistency")
        
        # 6. Process LLM segments
        if llm_segments:
            status.update(label=f"Translating {len(llm_segments)} segments...", state="running")
            
            llm_progress = st.progress(0)
            batch_translations_history = []
            
            for i in range(0, len(llm_segments), batch_size):
                batch = llm_segments[i:i + batch_size]
                batch_num = (i // batch_size) + 1
                total_batches = (len(llm_segments) + batch_size - 1) // batch_size
                
                st.write(f"📤 Batch {batch_num}/{total_batches} ({len(batch)} segments)")
                
                logger.log_batch_start(batch_num, batch)
                
                batch_tm = {seg.id: tm_context.get(seg.id, []) for seg in batch}
                batch_tb = {seg.id: tb_context.get(seg.id, []) for seg in batch}
                
                history_context = get_chat_history_context(
                    batch_translations_history, 
                    chat_history_length * batch_size
                )
                
                if history_context:
                    logger.log(f"Chat history: {len(history_context)} previous translations included")
                
                # Get reference samples for this batch
                reference_samples = ""
                
                # Use semantic matching if embedding matcher is ready
                if st.session_state.reference_embeddings_ready and st.session_state.embedding_matcher:
                    try:
                        # Get source texts from batch
                        source_texts = [seg.source for seg in batch]
                        
                        # Find semantically similar references for all segments in batch
                        matcher = st.session_state.embedding_matcher
                        matches_dict = matcher.find_similar_batch(
                            source_texts,
                            top_k=3,
                            min_similarity=0.35
                        )
                        
                        # Collect unique references
                        all_matches = []
                        seen_indices = set()
                        for seg_matches in matches_dict.values():
                            for m in seg_matches:
                                if m.index not in seen_indices:
                                    all_matches.append(m)
                                    seen_indices.add(m.index)
                        
                        # Sort by similarity and format
                        all_matches.sort(key=lambda x: x.similarity, reverse=True)
                        reference_samples = matcher.format_reference_context(all_matches[:8], max_chars=2000)
                        
                        if reference_samples:
                            logger.log(f"Semantic reference: {len(all_matches)} matches, {len(reference_samples)} chars")
                            
                    except Exception as e:
                        logger.log(f"Semantic reference error: {e}")
                        # Fallback to simple sampling
                        if st.session_state.reference_chunks:
                            reference_samples = get_reference_samples(
                                st.session_state.reference_chunks,
                                batch_num,
                                samples_per_batch=5,
                                max_chars=1500
                            )
                
                # Fallback: simple rotating samples (no embeddings)
                elif st.session_state.reference_chunks:
                    reference_samples = get_reference_samples(
                        st.session_state.reference_chunks,
                        batch_num,
                        samples_per_batch=5,
                        max_chars=1500
                    )
                    if reference_samples:
                        logger.log(f"Reference (rotating): {len(reference_samples)} chars of style samples")
                
                # Get DNT terms
                dnt_terms = st.session_state.dnt_terms if st.session_state.dnt_terms else None
                if dnt_terms:
                    logger.log(f"DNT list: {len(dnt_terms)} forbidden terms")
                
                prompt = prompt_builder.build_prompt(
                    config.SUPPORTED_LANGUAGES[src_code],
                    config.SUPPORTED_LANGUAGES[tgt_code],
                    batch, 
                    batch_tm, 
                    batch_tb,
                    chat_history=history_context,
                    reference_context=reference_samples,
                    dnt_terms=dnt_terms
                )
                
                try:
                    st.write("🔄 Calling LLM API...")
                    response_text, tokens = translator.translate_batch(prompt)

                    if response_text:
                        st.write("✅ LLM Response received")
                        logger.log_llm_interaction(prompt, response_text)

                        lines = response_text.strip().split('\n')
                        batch_results = []
                        parsed_translations = {}

                        for line in lines:
                            if line.startswith('[') and ']' in line:
                                try:
                                    seg_id = line[line.find('[')+1:line.find(']')]
                                    trans_text = line[line.find(']')+1:].strip()
                                    final_translations[seg_id] = trans_text
                                    parsed_translations[seg_id] = trans_text

                                    seg_obj = st.session_state.segment_objects.get(seg_id)
                                    if seg_obj:
                                        batch_results.append({
                                            'source': seg_obj.source,
                                            'target': trans_text
                                        })
                                except Exception:
                                    pass

                        # IMPROVEMENT: Log actual translations for transparency
                        if parsed_translations:
                            logger.log(f"Parsed Response (Batch {batch_num}):")
                            for seg_id in sorted(parsed_translations.keys(), key=lambda x: int(x) if x.isdigit() else 0):
                                trans_text = parsed_translations[seg_id]
                                # Truncate to 80 chars for readability
                                display_text = trans_text[:80] + "..." if len(trans_text) > 80 else trans_text
                                logger.log(f"  [{seg_id}] {display_text}")

                        batch_translations_history.extend(batch_results)
                    else:
                        st.error("❌ LLM returned empty response")
                        logger.log(f"ERROR: Batch {batch_num} - LLM returned empty response")
                        for seg in batch:
                            match_scores[seg.id] = 0
                        continue

                except AuthenticationError as e:
                    st.error(f"❌ Authentication Error: {str(e)}")
                    st.info("Possible causes: Invalid API key, expired token, rate limit")
                    logger.log(f"ERROR: Authentication failed - {str(e)}")
                    # Mark segments as failed
                    for seg in batch:
                        match_scores[seg.id] = 0
                    break  # Stop processing further batches

                except Exception as e:
                    err_msg = f"Batch {batch_num} failed: {str(e)}"
                    st.error(f"❌ {err_msg}")
                    logger.log(f"ERROR: {err_msg}")
                    for seg in batch:
                        match_scores[seg.id] = 0
                    continue  # Try next batch
                
                llm_progress.progress((i + len(batch)) / len(llm_segments))
        
        # 7. Save results
        duration = time.time() - start_time
        st.session_state.translation_results = final_translations
        st.session_state.translation_log = logger.get_content()
        st.session_state.chat_history = batch_translations_history if llm_segments else []
        st.session_state.segment_match_scores = match_scores  # Store match scores for memoQ metadata
        
        # IMPROVEMENT: Log final summary
        logger.log("\n" + "="*80)
        logger.log("TRANSLATION JOB SUMMARY")
        logger.log("="*80)
        logger.log(f"Total Segments: {total_segments}")
        logger.log(f"✓ Bypass (≥95%): {len(bypass_segments)} ({len(bypass_segments)/total_segments*100:.1f}%)")
        logger.log(f"✓ With TM Context (60-94%): {len(tm_context)} ({len(tm_context)/total_segments*100:.1f}%)")
        llm_only_count = len(llm_segments) - len(tm_context)
        logger.log(f"✓ LLM Only (<60%): {llm_only_count} ({llm_only_count/total_segments*100:.1f}%)")
        logger.log(f"Processing Time: {duration:.1f} seconds")
        logger.log(f"Batch Size: {batch_size} segments")
        num_batches = (len(llm_segments) + batch_size - 1) // batch_size if llm_segments else 0
        logger.log(f"Total Batches: {num_batches}")
        logger.log("="*80 + "\n")
        
        # Update session state with final log
        st.session_state.translation_log = logger.get_content()
        
        status.update(label="✅ Translation Complete!", state="complete")
        
        st.success(f"""
        **Translation Complete!**
        - {len(bypass_segments)} segments from TM (no API cost)
        - {len(llm_segments)} segments via LLM
        - {len(final_translations)} total translations
        """)


# --- UI Layout ---

st.title("🚀 Enhanced Translation Assistant")
st.markdown("AI-powered translation with TM, Termbase & Smart Prompt Builder")

tab1, tab2, tab3 = st.tabs(["📂 Workspace", "📊 Results", "✨ Prompt Builder"])

# === TAB 1: WORKSPACE ===
with tab1:
    col1, col2 = st.columns([2, 1])
    
    with col1:
        xliff_file = st.file_uploader(
            "📄 Upload Document (XLIFF)", 
            type=['xlf', 'xliff', 'mqxliff'],
            help="MemoQ XLIFF, Standard XLIFF"
        )
        
        if xliff_file:
            xliff_file.seek(0)
            detected_src, detected_tgt = XMLParser.detect_languages(xliff_file.getvalue())
            if detected_src and detected_tgt:
                st.session_state.detected_languages = {
                    'source': detected_src,
                    'target': detected_tgt
                }
                st.caption(f"🔍 Detected: {detected_src} → {detected_tgt}")
        
        # ==================== memoQ SERVER RESOURCES ====================
        st.markdown("---")
        st.markdown("##### 🔗 memoQ Server Resources")
        
        if st.session_state.memoq_connected and st.session_state.memoq_client:
            # Load TM/TB data
            selected_tms, selected_tbs = MemoQUI.show_memoq_data_loader(
                client=st.session_state.memoq_client,
                src_lang=src_code,
                tgt_lang=tgt_code
            )
            
            # Store selections
            st.session_state.selected_tm_guids = selected_tms
            st.session_state.selected_tb_guids = selected_tbs
            
            # Show status
            if selected_tms or selected_tbs:
                st.info(
                    f"✓ Using {len(selected_tms)} TM(s) and {len(selected_tbs)} TB(s) from memoQ Server"
                )
        else:
            st.warning("🔗 Not connected to memoQ Server. Configure connection in sidebar.")
        
        st.markdown("---")

        # ANALYSIS SCREEN - Show if file uploaded and TMs selected
        if xliff_file and len(st.session_state.selected_tm_guids) > 0:
            if not st.session_state.get('ready_to_translate', False):
                st.markdown("## 📋 File Analysis & Cost Estimate")
                st.markdown("---")

                # File info
                col_info1, col_info2 = st.columns(2)
                with col_info1:
                    st.info(f"📄 File: {xliff_file.name}")
                with col_info2:
                    st.success(f"✓ Using {len(st.session_state.selected_tm_guids)} TM(s) from memoQ Server")

                # Analyze button - show if no analysis results yet
                if st.session_state.analysis_results is None:
                    if st.button("🔍 Analyze File", type="primary", width="stretch"):
                        st.session_state.analysis_triggered = True

                        with st.status("Analyzing with memoQ Server TM...", expanded=True) as status:
                            st.write("📄 Parsing XLIFF...")
                            xliff_file.seek(0)
                            segments = XMLParser.parse_xliff(xliff_file.getvalue())
                            st.write(f"✅ Loaded {len(segments)} segments")

                            # Initialize analysis structure
                            analysis_by_level = {
                                '101% (Context)': {'segments': 0, 'words': 0},
                                '100%': {'segments': 0, 'words': 0},
                                '95%-99%': {'segments': 0, 'words': 0},
                                '85%-94%': {'segments': 0, 'words': 0},
                                '75%-84%': {'segments': 0, 'words': 0},
                                '50%-74%': {'segments': 0, 'words': 0},
                                'No match': {'segments': 0, 'words': 0}
                            }

                            total_words = 0
                            memoq_client = st.session_state.get('memoq_client')
                            memoq_tm_guids = st.session_state.selected_tm_guids

                            st.write("🔍 Analyzing TM matches...")
                            analysis_progress = st.progress(0)

                            # FIX-8: Calculate word counts with tag stripping
                            segment_word_counts = {}
                            for seg in segments:
                                clean_text = re.sub(r'<[^>]+>|\{\{\d+\}\}', '', seg.source).strip()
                                wc = len(clean_text.split()) if clean_text else 0
                                segment_word_counts[seg.id] = wc
                                total_words += wc

                            # FIX-7: Batch TM lookup instead of per-segment
                            segment_scores = {}  # seg.id -> best match score
                            BATCH_SIZE = 50

                            if memoq_client and memoq_tm_guids:
                                for tm_guid in memoq_tm_guids:
                                    # Only lookup segments that don't have a qualifying match yet
                                    remaining = [s for s in segments if segment_scores.get(s.id, 0) < match_threshold]
                                    if not remaining:
                                        break

                                    for batch_start in range(0, len(remaining), BATCH_SIZE):
                                        batch = remaining[batch_start:batch_start + BATCH_SIZE]
                                        normalized_sources = [normalize_segment_for_matching(s.source) for s in batch]

                                        try:
                                            results = memoq_client.lookup_segments(
                                                tm_guid, normalized_sources,
                                                src_lang=src_code, tgt_lang=tgt_code
                                            )

                                            if results:
                                                for idx, seg in enumerate(batch):
                                                    if idx in results:
                                                        tm_hits = results[idx]
                                                        if tm_hits:
                                                            score = tm_hits[0].similarity
                                                            if score > segment_scores.get(seg.id, 0):
                                                                segment_scores[seg.id] = score
                                        except Exception as e:
                                            pass  # Continue to next batch on error

                                        progress = (batch_start + len(batch)) / max(len(remaining), 1)
                                        analysis_progress.progress(min(progress, 1.0))

                            # Categorize all segments by match score
                            for seg in segments:
                                match_score = segment_scores.get(seg.id, 0)
                                word_count = segment_word_counts[seg.id]

                                if match_score > 100:
                                    analysis_by_level['101% (Context)']['segments'] += 1
                                    analysis_by_level['101% (Context)']['words'] += word_count
                                elif match_score == 100:
                                    analysis_by_level['100%']['segments'] += 1
                                    analysis_by_level['100%']['words'] += word_count
                                elif match_score >= 95:
                                    analysis_by_level['95%-99%']['segments'] += 1
                                    analysis_by_level['95%-99%']['words'] += word_count
                                elif match_score >= 85:
                                    analysis_by_level['85%-94%']['segments'] += 1
                                    analysis_by_level['85%-94%']['words'] += word_count
                                elif match_score >= 75:
                                    analysis_by_level['75%-84%']['segments'] += 1
                                    analysis_by_level['75%-84%']['words'] += word_count
                                elif match_score >= 50:
                                    analysis_by_level['50%-74%']['segments'] += 1
                                    analysis_by_level['50%-74%']['words'] += word_count
                                else:
                                    analysis_by_level['No match']['segments'] += 1
                                    analysis_by_level['No match']['words'] += word_count

                            analysis_progress.progress(1.0)

                            analysis_results = {
                                'total_segments': len(segments),
                                'total_words': total_words,
                                'by_level': analysis_by_level
                            }

                            st.session_state.analysis_results = analysis_results
                            status.update(label="✅ Analysis Complete!", state="complete")

                        st.rerun()

                # Show analysis results if available
                if st.session_state.analysis_results is not None:
                    show_analysis_screen(st.session_state.analysis_results)

                st.markdown("---")
        
        # Reference file for style/tone with semantic matching
        st.markdown("---")
        st.markdown("##### 📑 Semantic Reference (Optional)")
        
        reference_file = st.file_uploader(
            "Reference File (Target Language Only)",
            type=['txt', 'docx', 'pdf', 'html', 'htm', 'rtf', 'xlsx', 'xls'],
            help="Previously translated text for style/terminology reference. Supports TXT, DOCX, PDF, HTML, RTF, Excel."
        )
        
        if reference_file:
            reference_file.seek(0)
            chunks = parse_reference_file(reference_file.getvalue(), reference_file.name)
            st.session_state.reference_chunks = chunks
            
            if chunks:
                # Show cost estimate
                cost_info = get_embedding_cost_estimate(len(chunks), 100)  # Estimate for 100 segments
                
                col_ref1, col_ref2 = st.columns(2)
                with col_ref1:
                    st.metric("Reference Samples", len(chunks))
                with col_ref2:
                    st.metric("Est. Embedding Cost", cost_info['total_cost_formatted'])
                
                # Button to create embeddings
                if not st.session_state.reference_embeddings_ready:
                    if api_key:
                        if st.button("🧠 Create Semantic Index", type="secondary", width="stretch"):
                            with st.spinner("Creating embeddings... This may take a minute."):
                                try:
                                    matcher = EmbeddingMatcher(api_key)
                                    
                                    # Progress callback
                                    progress_bar = st.progress(0)
                                    def update_progress(current, total):
                                        progress_bar.progress(current / total if total > 0 else 0)
                                    
                                    count, was_cached = matcher.load_reference(chunks, update_progress)
                                    
                                    st.session_state.embedding_matcher = matcher
                                    st.session_state.reference_embeddings_ready = True
                                    
                                    if was_cached:
                                        st.success(f"✅ Loaded {count} cached embeddings")
                                    else:
                                        st.success(f"✅ Created {count} embeddings")
                                    st.rerun()
                                    
                                except Exception as e:
                                    st.error(f"Embedding error: {e}")
                    else:
                        st.warning("⚠️ API Key required for semantic matching")
                else:
                    st.success("✅ Semantic index ready")
                    if st.button("🔄 Reset Index"):
                        st.session_state.reference_embeddings_ready = False
                        st.session_state.embedding_matcher = None
                        st.rerun()
                
                with st.expander("Preview reference samples"):
                    for i, chunk in enumerate(chunks[:5]):
                        st.caption(f"{i+1}. {chunk[:100]}..." if len(chunk) > 100 else f"{i+1}. {chunk}")
                    if len(chunks) > 5:
                        st.caption(f"... and {len(chunks) - 5} more")
        
        st.markdown("---")
        
        # DNT (Do Not Translate) List
        dnt_file = st.file_uploader(
            "🚫 Do Not Translate List (TXT/CSV)",
            type=['txt', 'csv'],
            help="Terms that should remain in source language (brand names, product codes, etc.)"
        )
        
        if dnt_file:
            dnt_file.seek(0)
            terms = parse_dnt_file(dnt_file.getvalue(), dnt_file.name)
            st.session_state.dnt_terms = terms
            if terms:
                st.success(f"🚫 **{len(terms)}** forbidden terms loaded")
                with st.expander("Preview DNT terms"):
                    # Show first 20 terms
                    for term in terms[:20]:
                        st.caption(f"• {term}")
                    if len(terms) > 20:
                        st.caption(f"... and {len(terms) - 20} more")
        
        prompt_file = st.file_uploader(
            "📝 Custom Prompt Template (TXT)", 
            type=['txt'],
            help="Optional: Upload your own prompt template",
            disabled=st.session_state.use_generated_prompt
        )
        
        if st.session_state.use_generated_prompt:
            st.info("✨ Using prompt from Prompt Builder tab")
        
    with col2:
        st.info("""
        **How it works:**
        1. Segments ≥ Acceptance threshold → Direct TM
        2. Segments ≥ Match threshold → LLM with TM context
        3. Chat history provides consistency across batches
        4. Reference file provides style/tone guidance
        
        **Prompt Priority:**
        1. Generated prompt (from Prompt Builder)
        2. Custom file upload
        3. Default template
        """)
        
        if st.button("🚀 Start Translation", type="primary", width="stretch", disabled=not st.session_state.get('ready_to_translate', False)):
            if xliff_file:
                xliff_file.seek(0)
                
                custom_prompt = None
                if prompt_file and not st.session_state.use_generated_prompt:
                    prompt_file.seek(0)
                    custom_prompt = prompt_file.read().decode('utf-8')
                
                process_translation(
                    xliff_file.getvalue(),
                    tmx_bytes=None,
                    csv_bytes=None,
                    custom_prompt_content=custom_prompt,
                    memoq_tm_guids=st.session_state.selected_tm_guids,
                    memoq_tb_guids=st.session_state.selected_tb_guids
                )
            else:
                st.error("XLIFF file is required.")

# === TAB 2: RESULTS ===
with tab2:
    if st.session_state.translation_results:
        st.subheader("Translation Output")
        
        col_stat1, col_stat2, col_stat3 = st.columns(3)
        with col_stat1:
            st.metric("Total Segments", len(st.session_state.translation_results))
        with col_stat2:
            bypassed = st.session_state.bypass_stats.get('bypassed', 0)
            st.metric(f"From TM (≥{acceptance_threshold}%)", bypassed)
        with col_stat3:
            st.metric("Via LLM", st.session_state.bypass_stats.get('llm_sent', 0))
        
        st.divider()
        
        col_res1, col_res2 = st.columns(2)
        
        with col_res1:
            if xliff_file:
                xliff_file.seek(0)
                final_xml = XMLParser.update_xliff(
                    xliff_file.getvalue(),
                    st.session_state.translation_results,
                    st.session_state.get('segment_objects', {}),
                    match_scores=st.session_state.get('segment_match_scores', {})
                )
                
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                base_name = xliff_file.name.rsplit('.', 1)[0]
                extension = xliff_file.name.rsplit('.', 1)[1] if '.' in xliff_file.name else 'xliff'
                output_filename = f"{base_name}_translated_{timestamp}.{extension}"
                
                st.download_button(
                    "⬇️ Download Translated File",
                    final_xml,
                    file_name=output_filename,
                    mime="application/xml"
                )
                
        with col_res2:
            if st.session_state.translation_log:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                st.download_button(
                    "📜 Download Detailed Log",
                    st.session_state.translation_log,
                    file_name=f"translation_log_{timestamp}.txt",
                    mime="text/plain"
                )
        
        st.divider()
        st.subheader("Preview")
        
        preview_data = []
        for seg_id, trans in st.session_state.translation_results.items():
            seg_obj = st.session_state.segment_objects.get(seg_id)
            source = seg_obj.source if seg_obj else "N/A"
            preview_data.append({
                'ID': seg_id,
                'Source': source[:50] + '...' if len(source) > 50 else source,
                'Translation': trans[:50] + '...' if len(trans) > 50 else trans
            })
        
        df = pd.DataFrame(preview_data)
        st.dataframe(df, width="stretch")
    else:
        st.info("No results yet. Run translation in Workspace tab.")

# === TAB 3: PROMPT BUILDER ===
with tab3:
    st.subheader("✨ Smart Prompt Builder")
    st.markdown("Generate optimized prompts from Analysis Reports and Style Guides")
    
    col_pb1, col_pb2 = st.columns([1, 1])
    
    with col_pb1:
        st.markdown("#### 📄 Upload Documents")
        
        analysis_file = st.file_uploader(
            "📊 Analysis Report (DOCX)",
            type=['docx'],
            help="AICONTEXT analysis report",
            key="analysis_docx"
        )
        
        style_file = st.file_uploader(
            "📋 Style Guide (DOCX)",
            type=['docx'],
            help="Translation style guide",
            key="style_docx"
        )
        
        dnt_file = st.file_uploader(
            "🚫 Do Not Translate / Forbidden Terms (TXT/CSV)",
            type=['txt', 'csv'],
            help="List of terms to avoid in translation. One term per line or CSV format.",
            key="dnt_file"
        )
        
        # Parse DNT file
        forbidden_terms = []
        if dnt_file:
            dnt_file.seek(0)
            dnt_content = dnt_file.getvalue().decode('utf-8', errors='ignore')
            
            if dnt_file.name.endswith('.csv'):
                # Parse CSV - take first column
                for line in dnt_content.strip().split('\n'):
                    if line.strip():
                        # Handle comma-separated
                        parts = line.split(',')
                        term = parts[0].strip().strip('"').strip("'")
                        if term and not term.lower().startswith(('term', 'forbidden', 'dnt', '#')):
                            forbidden_terms.append(term)
            else:
                # Parse TXT - one term per line
                for line in dnt_content.strip().split('\n'):
                    term = line.strip()
                    if term and not term.startswith('#'):
                        forbidden_terms.append(term)
            
            st.success(f"🚫 **{len(forbidden_terms)}** forbidden terms loaded")
            with st.expander("Preview forbidden terms"):
                st.write(", ".join(forbidden_terms[:20]) + ("..." if len(forbidden_terms) > 20 else ""))
        
        # Analyze uploaded files
        analysis_result = None
        style_result = None
        
        if analysis_file:
            analysis_file.seek(0)
            analysis_result = DocumentAnalyzer.analyze_file(
                analysis_file.getvalue(), 
                analysis_file.name
            )
            
            with st.expander("📊 Analysis Report Extracted Data", expanded=True):
                if analysis_result.domain:
                    st.success(f"**Domain:** {analysis_result.domain}")
                if analysis_result.domain_composition:
                    st.write("**Domain Composition:**")
                    for comp in analysis_result.domain_composition:
                        st.write(f"  • {comp}")
                if analysis_result.terminology_categories:
                    st.write(f"**Terminology:** {len(analysis_result.terminology_categories)} categories")
                if analysis_result.critical_numbers:
                    st.write(f"**Critical Numbers:** {len(analysis_result.critical_numbers)} items")
                    
        if style_file:
            style_file.seek(0)
            style_result = DocumentAnalyzer.analyze_file(
                style_file.getvalue(),
                style_file.name
            )
            
            with st.expander("📋 Style Guide Extracted Data", expanded=True):
                if style_result.style_rules:
                    st.write(f"**Style Rules:** {len(style_result.style_rules)} rules")
                if style_result.formatting_rules:
                    st.write(f"**Formatting Rules:** {len(style_result.formatting_rules)} rules")
                if style_result.gender_inclusivity:
                    st.write(f"**Gender/Inclusivity:** {len(style_result.gender_inclusivity)} rules")
                if style_result.do_not_translate:
                    st.write(f"**DNT Items:** {len(style_result.do_not_translate)} items")
        
        st.divider()
        
        # Generate button
        if st.button("🔮 Generate Prompt", type="primary", width="stretch", 
                     disabled=(not analysis_file and not style_file and not dnt_file)):
            
            prompt, metadata = PromptGenerator.generate(
                analysis=analysis_result,
                style_guide=style_result,
                source_lang=config.SUPPORTED_LANGUAGES[src_code],
                target_lang=config.SUPPORTED_LANGUAGES[tgt_code],
                forbidden_terms=forbidden_terms
            )
            
            st.session_state.generated_prompt = prompt
            st.session_state.prompt_metadata = metadata
            st.success("✅ Prompt generated!")
    
    with col_pb2:
        st.markdown("#### 📝 Generated Prompt")
        
        if st.session_state.generated_prompt:
            # Show metadata
            meta = st.session_state.prompt_metadata
            col_m1, col_m2, col_m3, col_m4 = st.columns(4)
            with col_m1:
                st.metric("Style Rules", meta.get('style_rules_count', 0))
            with col_m2:
                st.metric("Term Categories", meta.get('terminology_categories', 0))
            with col_m3:
                st.metric("Format Rules", meta.get('formatting_rules_count', 0))
            with col_m4:
                st.metric("🚫 Forbidden", meta.get('forbidden_terms_count', 0))
            
            # Editable prompt
            edited_prompt = st.text_area(
                "Edit prompt (optional):",
                value=st.session_state.generated_prompt,
                height=400,
                key="prompt_editor"
            )
            
            # Update if edited
            if edited_prompt != st.session_state.generated_prompt:
                st.session_state.generated_prompt = edited_prompt
            
            st.divider()
            
            # Action buttons
            col_act1, col_act2, col_act3 = st.columns(3)
            
            with col_act1:
                if st.button("✅ Use This Prompt", type="primary", width="stretch"):
                    st.session_state.use_generated_prompt = True
                    st.success("Prompt activated! Go to Workspace tab to start translation.")
                    
            with col_act2:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                st.download_button(
                    "⬇️ Download",
                    st.session_state.generated_prompt,
                    file_name=f"cat_tool_prompt_{timestamp}.txt",
                    mime="text/plain",
                    width="stretch"
                )
            
            with col_act3:
                if st.button("🗑️ Clear", width="stretch"):
                    st.session_state.generated_prompt = None
                    st.session_state.prompt_metadata = {}
                    st.session_state.use_generated_prompt = False
                    st.rerun()
        else:
            st.info("""
            **How to use:**
            1. Upload Analysis Report (AICONTEXT output) and/or Style Guide
            2. Click "Generate Prompt"
            3. Review and edit if needed
            4. Click "Use This Prompt" to activate
            5. Go to Workspace tab and start translation
            
            **Extracted elements:**
            - Domain & context from Analysis Report
            - Technical protocols (decimal, units, etc.)
            - Style rules from Style Guide
            - Formatting rules
            - Terminology categories
            """)
