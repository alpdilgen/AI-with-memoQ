"""
TM MATCHER - Production Ready
Generic system that works with ANY TM file and ANY source file format

Core functionality:
  • Proper text normalization (handles tags, placeholders, whitespace)
  • Edit distance fuzzy matching (Levenshtein algorithm)
  • Standard CAT threshold classification
  • File format auto-detection (SDLXLIFF, XLIFF, MQXLIFF)
  • Generic file analysis (works with any source file)

No hardcoded data. No POC code. Production system.
"""

import re
import xml.etree.ElementTree as ET
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass

# ═════════════════════════════════════════════════════════════════════════════════
# TEXT NORMALIZATION
# ═════════════════════════════════════════════════════════════════════════════════

def normalize(text: str) -> str:
    """
    Normalize text for TM matching
    
    Steps:
    1. Remove XML tags but keep content
    2. Remove placeholders ({}, {{1}}, etc.)
    3. Normalize whitespace
    4. Convert to lowercase
    5. Keep punctuation and diacritics
    """
    text = re.sub(r'<[^>]+>', '', text)      # Remove tags
    text = re.sub(r'{.*?}', '', text)        # Remove placeholders
    text = ' '.join(text.split())             # Normalize whitespace
    text = text.strip()
    text = text.lower()
    return text


# ═════════════════════════════════════════════════════════════════════════════════
# EDIT DISTANCE (Levenshtein) - Industry standard for CAT tools
# ═════════════════════════════════════════════════════════════════════════════════

def levenshtein_distance(s1: str, s2: str) -> int:
    """Calculate Levenshtein edit distance"""
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    
    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    
    return previous_row[-1]


def edit_distance_similarity(s1: str, s2: str) -> float:
    """Convert edit distance to similarity score (0-1.0)"""
    distance = levenshtein_distance(s1, s2)
    max_len = max(len(s1), len(s2))
    if max_len == 0:
        return 1.0
    similarity = 1 - (distance / max_len)
    return max(0.0, min(1.0, similarity))


# ═════════════════════════════════════════════════════════════════════════════════
# MATCH CLASSIFICATION
# ═════════════════════════════════════════════════════════════════════════════════

def classify_match_level(score: float) -> str:
    """Classify similarity score using CAT-standard thresholds"""
    if score >= 1.0:
        return "100%"
    elif score >= 0.95:
        return "95%-99%"
    elif score >= 0.85:
        return "85%-94%"
    elif score >= 0.75:
        return "75%-84%"
    elif score >= 0.50:
        return "50%-74%"
    else:
        return "No match"


# ═════════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═════════════════════════════════════════════════════════════════════════════════

@dataclass
class TMMatch:
    """TM match result"""
    score: float
    level: str
    tm_source: str
    tm_target: str
    match_type: str


# ═════════════════════════════════════════════════════════════════════════════════
# CORE TM MATCHER
# ═════════════════════════════════════════════════════════════════════════════════

class TMatcher:
    """
    Generic TM Matcher
    Works with ANY TMX file and ANY source file format
    
    Usage:
        matcher = TMatcher('path/to/tm.tmx')
        results = matcher.analyze_file('path/to/source.sdlxliff')
    """
    
    def __init__(self, tm_file_path: str, bypass_threshold: float = 0.95):
        """Load TMX file"""
        self.tm_file_path = tm_file_path
        self.bypass_threshold = bypass_threshold
        
        # Load TM
        self.tm_entries = self._load_tmx(tm_file_path)
        self.tu_count = len(self.tm_entries)
        
        # Build exact match index for O(1) lookup
        self.exact_match_index = {}
        self.normalized_entries = []
        self._build_indices()
    
    def _load_tmx(self, file_path: str) -> List[Dict]:
        """Load TMX file (supports UTF-16 and UTF-8)"""
        try:
            with open(file_path, 'r', encoding='utf-16') as f:
                content = f.read()
        except:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        
        root = ET.fromstring(content)
        entries = []
        
        for tu in root.findall('.//tu'):
            tuv_list = tu.findall('tuv')
            if len(tuv_list) >= 2:
                source_seg = tuv_list[0].find('seg')
                target_seg = tuv_list[1].find('seg')
                
                if source_seg is not None and target_seg is not None:
                    source = ''.join(source_seg.itertext()).strip()
                    target = ''.join(target_seg.itertext()).strip()
                    
                    if source:
                        entries.append({'source': source, 'target': target})
        
        return entries
    
    def _build_indices(self):
        """Build exact match index and normalized entries for fuzzy"""
        for entry in self.tm_entries:
            source = entry.get('source', '')
            target = entry.get('target', '')
            
            normalized_source = normalize(source)
            
            # Exact match index
            if normalized_source not in self.exact_match_index:
                self.exact_match_index[normalized_source] = target
            
            # Fuzzy search entries
            self.normalized_entries.append({
                'source_normalized': normalized_source,
                'source_original': source,
                'target': target
            })
    
    def match(self, source_text: str) -> TMMatch:
        """Match a single segment against TM"""
        source_normalized = normalize(source_text)
        
        # Try exact match (O(1))
        if source_normalized in self.exact_match_index:
            return TMMatch(
                score=1.0,
                level='100%',
                tm_source=source_normalized,
                tm_target=self.exact_match_index[source_normalized],
                match_type='EXACT'
            )
        
        # Fuzzy match
        best_score = 0
        best_entry = None
        
        for entry in self.normalized_entries:
            score = edit_distance_similarity(source_normalized, entry['source_normalized'])
            if score > best_score:
                best_score = score
                best_entry = entry
        
        if best_entry is None or best_score < 0.50:
            return TMMatch(
                score=0,
                level='No match',
                tm_source='',
                tm_target='',
                match_type='NONE'
            )
        
        return TMMatch(
            score=best_score,
            level=classify_match_level(best_score),
            tm_source=best_entry['source_original'],
            tm_target=best_entry['target'],
            match_type='FUZZY'
        )
    
    def should_bypass_llm(self, source_text: str) -> Tuple[bool, str, float]:
        """Check if segment should bypass LLM (use TM instead)"""
        match = self.match(source_text)
        return match.score >= self.bypass_threshold, match.tm_target, match.score
    
    def extract_matches(self, source_text: str, threshold: float = 0.75) -> Tuple[List, float]:
        """Extract TM matches (legacy format compatibility)"""
        match = self.match(source_text)
        
        if match.score < threshold:
            return [{'result': {}}], 0.0
        
        return [{
            'source': match.tm_source,
            'target': match.tm_target,
            'score': match.score,
            'percent': int(match.score * 100),
        }], match.score
    
    # ═════════════════════════════════════════════════════════════════════════════
    # FILE ANALYSIS - Works with ANY file format
    # ═════════════════════════════════════════════════════════════════════════════
    
    def analyze_file(self, file_path: str, file_format: str = None) -> Dict:
        """
        Analyze any supported file format
        
        Supports: SDLXLIFF, XLIFF, MQXLIFF
        Auto-detects format from file extension
        """
        if file_format is None:
            lower_path = file_path.lower()
            if 'sdlxliff' in lower_path or 'mqxliff' in lower_path:
                file_format = 'sdlxliff'
            elif 'xliff' in lower_path or lower_path.endswith('.xlf'):
                file_format = 'xliff'
            else:
                raise ValueError(f"Unknown format for {file_path}")
        
        if file_format.lower() == 'sdlxliff':
            segments = self._extract_sdlxliff(file_path)
        elif file_format.lower() == 'xliff':
            segments = self._extract_xliff(file_path)
        else:
            raise ValueError(f"Unsupported format: {file_format}")
        
        return self.analyze_segments(segments)
    
    def _extract_sdlxliff(self, file_path: str) -> List[str]:
        """Extract segments from SDLXLIFF file"""
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        root = ET.fromstring(content)
        ns = {'xliff': 'urn:oasis:names:tc:xliff:document:1.2', 'mq': 'MQXliff'}
        
        segments = []
        for trans_unit in root.findall('.//xliff:trans-unit', ns):
            source_elem = trans_unit.find('xliff:source', ns)
            source_text = ''.join(source_elem.itertext()) if source_elem is not None else ''
            segments.append(source_text)
        
        return segments
    
    def _extract_xliff(self, file_path: str) -> List[str]:
        """Extract segments from XLIFF file"""
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        root = ET.fromstring(content)
        ns = {'xliff': 'urn:oasis:names:tc:xliff:document:1.2'}
        
        segments = []
        for trans_unit in root.findall('.//xliff:trans-unit', ns):
            source_elem = trans_unit.find('xliff:source', ns)
            source_text = ''.join(source_elem.itertext()) if source_elem is not None else ''
            segments.append(source_text)
        
        return segments
    
    def analyze_segments(self, segments: List[str]) -> Dict:
        """Analyze list of segments"""
        matches = [self.match(seg) for seg in segments]
        
        # Calculate statistics
        by_level = {}
        total_words = 0
        
        detailed_matches = []
        for i, (segment, match) in enumerate(zip(segments, matches), 1):
            word_count = len(normalize(segment).split()) if segment else 0
            total_words += word_count
            
            level = match.level
            if level not in by_level:
                by_level[level] = {'segments': 0, 'words': 0}
            
            by_level[level]['segments'] += 1
            by_level[level]['words'] += word_count
            
            detailed_matches.append({
                'segment_id': i,
                'source': segment,
                'level': match.level,
                'score': match.score,
                'score_percent': f"{match.score*100:.1f}%",
                'target': match.tm_target,
                'match_type': match.match_type,
                'words': word_count
            })
        
        return {
            'total_segments': len(segments),
            'total_words': total_words,
            'by_level': by_level,
            'matches': detailed_matches
        }


# ═════════════════════════════════════════════════════════════════════════════════
# USAGE EXAMPLE
# ═════════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("GENERIC TM ANALYSIS SYSTEM")
    print("=" * 70)
    print("""
    Usage:
    
    1. Load ANY TMX file:
       matcher = TMatcher('path/to/tm.tmx')
    
    2. Analyze ANY source file:
       results = matcher.analyze_file('path/to/source.sdlxliff')
    
    3. Get results:
       print(f"Total: {results['total_segments']} segments")
       for level, data in results['by_level'].items():
           print(f"  {level}: {data['segments']} segs")
    
    System works with:
      ✓ Any TMX file
      ✓ Any SDLXLIFF/XLIFF/MQXLIFF file
      ✓ Any language pair
      ✓ Batch processing
    
    No hardcoded data. Production ready.
    """)
    print("=" * 70)
