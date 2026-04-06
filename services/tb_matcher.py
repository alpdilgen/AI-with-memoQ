import pandas as pd
import re
from services.caching import CacheManager
from models.entities import TermMatch
from typing import List, Tuple, Optional

class TBMatcher:
    """
    Termbase matcher with support for:
    - Simple 2-column CSV (source, target)
    - MemoQ exported termbase (multi-column)
    - Auto-detection of column format
    """
    
    def __init__(self, csv_file):
        self.df = CacheManager.load_tb_data(csv_file)
        self.src_col, self.tgt_col = self._detect_columns()
        self.term_count = self._count_valid_terms()
    
    def _detect_columns(self) -> Tuple[Optional[str], Optional[str]]:
        """
        Auto-detect source and target columns.
        Supports:
        - Simple CSV: first 2 columns
        - MemoQ TB: columns containing language names
        """
        if self.df.empty:
            return None, None
        
        columns = self.df.columns.tolist()
        
        src_col = None
        tgt_col = None
        
        # Strategy 1: Look for exact MemoQ column names (without suffix)
        # MemoQ exports have columns like "English_United_Kingdom", "Turkish", etc.
        for col in columns:
            # Skip columns with suffixes (.1, .2, etc.) - take only the first occurrence
            if '.' in col and col.split('.')[-1].isdigit():
                continue
            
            col_lower = col.lower()
            
            # Skip definition, info, and example columns
            if any(skip in col_lower for skip in ['_def', '_info', '_example', 'term_info', 'term_example']):
                continue
            
            # Source language detection
            if src_col is None:
                if any(lang in col_lower for lang in ['english', 'en_', 'en-', 'source', 'german', 'de_', 'french', 'fr_']):
                    src_col = col
            
            # Target language detection  
            if tgt_col is None:
                if any(lang in col_lower for lang in ['turkish', 'tr_', 'tr-', 'target']):
                    tgt_col = col
        
        # Strategy 2: If target not found, look for other common target languages
        if src_col and tgt_col is None:
            for col in columns:
                if '.' in col and col.split('.')[-1].isdigit():
                    continue
                col_lower = col.lower()
                if any(skip in col_lower for skip in ['_def', '_info', '_example']):
                    continue
                # Common target languages
                if any(lang in col_lower for lang in ['german', 'de_', 'french', 'fr_', 'spanish', 'es_', 'italian', 'it_']):
                    if col != src_col:
                        tgt_col = col
                        break
        
        # Strategy 3: Fallback to simple 2-column format
        if src_col is None or tgt_col is None:
            if len(columns) >= 2:
                # Filter out metadata columns
                data_cols = [c for c in columns if not any(skip in c.lower() for skip in ['entry_', 'term_info', 'term_example', '_def'])]
                if len(data_cols) >= 2:
                    src_col = data_cols[0]
                    tgt_col = data_cols[1]
                else:
                    src_col = columns[0]
                    tgt_col = columns[1]
        
        return src_col, tgt_col
    
    def _count_valid_terms(self) -> int:
        """Count valid term pairs"""
        if self.df.empty or self.src_col is None or self.tgt_col is None:
            return 0
        
        valid = self.df[
            (self.df[self.src_col].notna()) & 
            (self.df[self.src_col].str.strip() != '') &
            (self.df[self.tgt_col].notna()) &
            (self.df[self.tgt_col].str.strip() != '')
        ]
        return len(valid)
    
    def get_all_terms(self) -> List[Tuple[str, str]]:
        """Get all valid term pairs"""
        if self.df.empty or self.src_col is None or self.tgt_col is None:
            return []
        
        terms = []
        for _, row in self.df.iterrows():
            src = str(row[self.src_col]).strip() if pd.notna(row[self.src_col]) else ''
            tgt = str(row[self.tgt_col]).strip() if pd.notna(row[self.tgt_col]) else ''
            
            if src and tgt and src != 'nan' and tgt != 'nan':
                terms.append((src, tgt))
        
        return terms
        
    def extract_matches(self, text: str) -> List[TermMatch]:
        """
        Find all termbase matches in the given text.
        
        Args:
            text: Source text to search for terms
            
        Returns:
            List of TermMatch objects for found terms
        """
        matches = []
        
        if self.df.empty or self.src_col is None or self.tgt_col is None:
            return matches
        
        # Get unique terms and sort by length (longer first)
        terms = self.get_all_terms()
        terms_sorted = sorted(terms, key=lambda x: len(x[0]), reverse=True)
        
        # Track already matched terms to avoid duplicates
        matched_sources = set()
        
        for term_src, term_tgt in terms_sorted:
            if term_src in matched_sources:
                continue
                
            if not term_src or len(term_src) < 2:
                continue
            
            # Word boundary regex match (case insensitive)
            try:
                pattern = r'\b' + re.escape(term_src) + r'\b'
                if re.search(pattern, text, re.IGNORECASE):
                    matches.append(TermMatch(source=term_src, target=term_tgt))
                    matched_sources.add(term_src)
            except re.error:
                # Skip invalid regex patterns
                continue
        
        return matches
