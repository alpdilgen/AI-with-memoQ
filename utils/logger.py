"""
Enhanced Transaction Logger for detailed translation workflow logging
Matches competitor's detailed logging standards
"""

from datetime import datetime
from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)


class TransactionLogger:
    """Enhanced logger with detailed segment, context, and response tracking"""
    
    def __init__(self):
        """Initialize transaction logger"""
        self.logs = []
        self.start_time = datetime.now()
        self.total_segments = 0
        self.bypass_count = 0
        self.llm_count = 0
        self.processed_count = 0
        
    def log(self, message: str):
        """Add timestamped log entry"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"{timestamp} | {message}"
        self.logs.append(entry)
        logger.info(message)
    
    # ===== INITIALIZATION =====
    
    def init_job(self, total_segments: int, src_lang: str, tgt_lang: str, 
                 model: str, acceptance_threshold: int, match_threshold: int, 
                 chat_history_length: int, use_memoq: bool = False, 
                 memoq_tm_count: int = 0, memoq_tb_count: int = 0):
        """Log job initialization"""
        self.total_segments = total_segments
        self.log(f"Started translation job for {total_segments} segments.")
        self.log(f"Source: {src_lang} | Target: {tgt_lang} | Model: {model}")
        self.log(f"TM Acceptance: ≥{acceptance_threshold}% | TM Match: ≥{match_threshold}%")
        self.log(f"Chat History Length: {chat_history_length}")
        
        if use_memoq:
            self.log(f"memoQ Server: {memoq_tm_count} TMs, {memoq_tb_count} TBs")
    
    def log_prompt_template(self, template_source: str):
        """Log which prompt template is being used"""
        self.log(f"Using {template_source} prompt template.")
    
    # ===== SEGMENT DETAILS =====
    
    def log_segment_content(self, segment_id: str, source_text: str, limit: int = 100):
        """Log segment content before processing"""
        truncated = source_text[:limit] + "..." if len(source_text) > limit else source_text
        self.log(f"[{segment_id}] Source: {truncated}")
    
    def log_segment_analysis(self, segment_id: str, match_score: int, source: str = "TM", action: str = None):
        """Log segment match analysis"""
        if action:
            self.log(f"[{segment_id}] {source} match score: {match_score}% → {action}")
        else:
            self.log(f"[{segment_id}] {source} match score: {match_score}%")
    
    def log_segment_bypass(self, segment_id: str, match_score: int, match_type: str = "TM"):
        """Log when segment bypasses LLM"""
        self.log(f"[{segment_id}] BYPASS ({match_score}% {match_type} match)")
        self.bypass_count += 1
    
    def log_segment_to_llm(self, segment_id: str, match_score: int = None, match_type: str = "TM"):
        """Log when segment goes to LLM"""
        if match_score:
            self.log(f"[{segment_id}] CONTEXT ({match_score}% {match_type} fuzzy match)")
        else:
            self.log(f"[{segment_id}] No matches found → sending to LLM")
        self.llm_count += 1
    
    # ===== BATCH CONTEXT LOGGING =====
    
    def log_tm_matches(self, tm_context: Dict[str, List]):
        """Log TM matches found"""
        if not tm_context:
            self.log("TM Context: No matches found")
            return
        
        total_matches = sum(len(matches) for matches in tm_context.values() if matches)
        segments_with_matches = len([m for m in tm_context.values() if m])
        self.log(f"TM Context: {total_matches} matches across {segments_with_matches} segments")
        
        for seg_id, matches in tm_context.items():
            if matches:
                match_str = ", ".join([f"[{m.similarity}%]" for m in matches[:3]])
                self.log(f"  [{seg_id}] {match_str}")
    
    def log_tb_matches(self, tb_context: Dict[str, List]):
        """Log TB matches found"""
        if not tb_context:
            self.log("TB Context: No terminology found")
            return
        
        total_terms = sum(len(terms) for terms in tb_context.values() if terms)
        segments_with_terms = len([t for t in tb_context.values() if t])
        self.log(f"TB Context: {total_terms} terms across {segments_with_terms} segments")
        
        for seg_id, terms in tb_context.items():
            if terms:
                term_str = ", ".join([f"{t.source}={t.target}" for t in terms[:3]])
                self.log(f"  [{seg_id}] {term_str}")
    
    def log_batch_context(self, batch_num: int, batch_size: int, 
                         tm_count: int = 0, tb_count: int = 0, dnt_count: int = 0):
        """Log batch context summary"""
        self.log(f"\n{'='*80}")
        self.log(f"=== BATCH {batch_num} ===")
        self.log(f"Segments in batch: {batch_size}")
        
        if tm_count > 0:
            self.log(f"TM Context: {tm_count} matches")
        if tb_count > 0:
            self.log(f"TB Context: {tb_count} terms")
        if dnt_count > 0:
            self.log(f"DNT Check: {dnt_count} forbidden terms found")
        
        self.log(f"{'='*80}\n")
    
    # ===== PROMPT LOGGING =====
    
    def log_prompt_sent(self, batch_num: int, prompt: str, max_chars: int = 500):
        """Log prompt being sent to LLM"""
        truncated = prompt[:max_chars] + "..." if len(prompt) > max_chars else prompt
        self.log(f"Prompt for batch {batch_num}: {truncated}")
        self.log(f"Prompt size: {len(prompt)} characters")
    
    def log_batch_start(self, batch_num: int, batch: List):
        """Log batch processing start"""
        self.log(f"Batch {batch_num}: {len(batch)} segments to LLM")
    
    def log_batch_segments(self, batch_num: int, batch: List):
        """Log all segments in batch with details"""
        self.log(f"Segments {batch_num}:")
        for seg in batch:
            self.log(f"  [{seg.id}] {seg.source[:80]}")
    
    # ===== RESPONSE LOGGING =====
    
    def log_parsed_response(self, batch_num: int, translations: Dict[str, str]):
        """Log parsed LLM response"""
        self.log(f"Parsed Response (Batch {batch_num}):")
        for seg_id, trans_text in translations.items():
            truncated = trans_text[:80] + "..." if len(trans_text) > 80 else trans_text
            self.log(f"  [{seg_id}] {truncated}")
    
    def log_llm_interaction(self, prompt: str, response: str):
        """Log full LLM interaction (for detailed analysis)"""
        self.log(f"LLM Prompt length: {len(prompt)} chars")
        self.log(f"LLM Response length: {len(response)} chars")
    
    # ===== PROGRESS TRACKING =====
    
    def log_progress(self, processed: int, total: int):
        """Log processing progress"""
        percentage = (processed / total * 100) if total > 0 else 0
        self.log(f"Progress: Processed {processed} of {total} segments ({percentage:.1f}%)")
        self.processed_count = processed
    
    def log_analysis_complete(self, bypass_count: int, llm_count: int, total: int):
        """Log analysis completion"""
        self.log(f"Analysis complete: {bypass_count} bypass, {llm_count} LLM, {total} total")
        
        # Verify counts match
        if bypass_count + llm_count != total:
            self.log(f"⚠️ WARNING: Count mismatch! {bypass_count} + {llm_count} ≠ {total}")
    
    # ===== SUMMARY LOGGING =====
    
    def log_summary(self, bypass_count: int, llm_count: int, total_segments: int, duration_seconds: float = None):
        """Log final translation summary"""
        self.log("\n" + "="*80)
        self.log("SUMMARY")
        self.log("="*80)
        
        # Verify counts
        if bypass_count + llm_count == total_segments:
            self.log(f"✓ Total Segments: {total_segments}")
            self.log(f"✓ From TM (bypass): {bypass_count} ({bypass_count/total_segments*100:.1f}%)")
            self.log(f"✓ Via LLM: {llm_count} ({llm_count/total_segments*100:.1f}%)")
        else:
            self.log(f"✗ SEGMENT COUNT MISMATCH!")
            self.log(f"  Total segments: {total_segments}")
            self.log(f"  Bypass: {bypass_count}")
            self.log(f"  LLM: {llm_count}")
            self.log(f"  Sum: {bypass_count + llm_count} (should be {total_segments})")
        
        if duration_seconds:
            self.log(f"Duration: {duration_seconds:.1f}s")
        
        self.log("="*80 + "\n")
    
    def log_error(self, batch_num: int, error_msg: str):
        """Log error"""
        self.log(f"ERROR - Batch {batch_num}: {error_msg}")
    
    def log_file_saved(self, filepath: str):
        """Log file saved"""
        self.log(f"File saved: {filepath}")
    
    # ===== UTILITY METHODS =====
    
    def get_content(self) -> str:
        """Get all log content as string"""
        return "\n".join(self.logs)
    
    def get_log_lines(self) -> List[str]:
        """Get all log lines as list"""
        return self.logs
    
    def save_to_file(self, filepath: str):
        """Save logs to file"""
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(self.get_content())
            logger.info(f"Logs saved to {filepath}")
        except Exception as e:
            logger.error(f"Failed to save logs: {e}")
    
    def clear(self):
        """Clear all logs"""
        self.logs = []
        self.bypass_count = 0
        self.llm_count = 0
        self.processed_count = 0
