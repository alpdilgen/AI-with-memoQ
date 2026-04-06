"""
Data models for translation segments, TM matches, and TB matches
"""

from dataclasses import dataclass
from typing import List, Optional, Dict, Any


@dataclass
class TranslationSegment:
    """Represents a single translation segment"""
    id: str
    source: str
    target: str = ""
    tag_map: Optional[dict] = None


@dataclass
class TMMatch:
    """Universal TM Match object - works with TMX, memoQ, local files"""
    source_text: str
    target_text: str
    similarity: int
    match_type: str = "FUZZY"
    source_file: Optional[str] = None
    project: Optional[str] = None
    domain: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    
    def __post_init__(self):
        """Clean up text"""
        self.source_text = self.source_text.strip() if self.source_text else ""
        self.target_text = self.target_text.strip() if self.target_text else ""
        if self.metadata is None:
            self.metadata = {}
    
    def __repr__(self):
        src = self.source_text[:40] if self.source_text else "empty"
        tgt = self.target_text[:40] if self.target_text else "empty"
        return f"TMMatch('{src}...' → '{tgt}...' [{self.match_type} {self.similarity}%])"
    
    def __hash__(self):
        return hash((self.source_text, self.target_text, self.similarity))
    
    def __eq__(self, other):
        if not isinstance(other, TMMatch):
            return False
        return (self.source_text == other.source_text and
                self.target_text == other.target_text and
                self.similarity == other.similarity)


@dataclass
class TermMatch:
    """Represents a termbase (terminology) match"""
    source: str
    target: str
    context: Optional[str] = None
    part_of_speech: Optional[str] = None
    field: Optional[str] = None
    definition: Optional[str] = None
    source_language: Optional[str] = None
    target_language: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    
    def __post_init__(self):
        """Clean and validate term data"""
        self.source = self.source.strip() if self.source else ""
        self.target = self.target.strip() if self.target else ""
        if self.metadata is None:
            self.metadata = {}
    
    def is_valid(self) -> bool:
        """Check if term has valid source and target"""
        return bool(self.source and self.target)
    
    def __repr__(self):
        return f"TermMatch({self.source} = {self.target})"
    
    def __hash__(self):
        return hash((self.source, self.target))
    
    def __eq__(self, other):
        if not isinstance(other, TermMatch):
            return False
        return self.source == other.source and self.target == other.target


@dataclass
class BatchResult:
    """Result of a translation batch"""
    batch_num: int
    segments: List[TranslationSegment]
    translations: Dict[str, str]
    tokens_used: int = 0
    processing_time: float = 0.0
    errors: Optional[List[str]] = None
    
    def __post_init__(self):
        if self.errors is None:
            self.errors = []
    
    def success_count(self) -> int:
        """Number of successfully translated segments"""
        return len(self.translations)
    
    def error_count(self) -> int:
        """Number of failed translations"""
        return len(self.errors) if self.errors else 0
    
    def __repr__(self):
        return f"BatchResult(batch={self.batch_num}, translations={self.success_count()}, errors={self.error_count()})"


@dataclass
class TranslationMetadata:
    """Metadata for a translation job"""
    source_language: str
    target_language: str
    source_file_name: str
    total_segments: int
    bypass_segments: int = 0
    llm_segments: int = 0
    total_cost: float = 0.0
    total_tokens: int = 0
    processing_time: float = 0.0
    tm_used: bool = False
    tb_used: bool = False
    dnt_used: bool = False
    memoq_used: bool = False
    reference_used: bool = False


@dataclass
class SegmentAnalysis:
    """Analysis of a single segment"""
    segment_id: str
    source_text: str
    local_tm_match: Optional[TMMatch] = None
    local_tm_matches: Optional[List[TMMatch]] = None
    memoq_tm_match: Optional[TMMatch] = None
    memoq_tm_matches: Optional[List[TMMatch]] = None
    tb_matches: Optional[List[TermMatch]] = None
    dnt_terms_found: Optional[List[str]] = None
    should_bypass: bool = False
    bypass_reason: Optional[str] = None
    match_percentage: int = 0
    
    def __post_init__(self):
        if self.local_tm_matches is None:
            self.local_tm_matches = []
        if self.memoq_tm_matches is None:
            self.memoq_tm_matches = []
        if self.tb_matches is None:
            self.tb_matches = []
        if self.dnt_terms_found is None:
            self.dnt_terms_found = []
    
    def get_best_match(self) -> Optional[TMMatch]:
        """Get highest match available"""
        all_matches = (self.local_tm_matches or []) + (self.memoq_tm_matches or [])
        if all_matches:
            return max(all_matches, key=lambda m: m.similarity)
        return None
    
    def __repr__(self):
        action = "BYPASS" if self.should_bypass else "LLM"
        return f"SegmentAnalysis({self.segment_id}, {action}, {self.match_percentage}%)"


@dataclass
class QAError:
    """Represents a QA error found and optionally fixed"""
    code: int
    segment_id: str
    description: str
    status: str = "detected"
    original_target: str = ""
    fixed_target: str = ""

    def __repr__(self):
        return f"QAError({self.code}: {self.description} [{self.status}])"
