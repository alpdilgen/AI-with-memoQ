"""
QA Engine - Post-translation quality assurance checks.
6 checks: terminology, numbers, tags, empty, punctuation, consistency.
All checks are enabled by default.
"""

import re
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class QAIssue:
    """A single QA issue found in a segment."""
    segment_index: int
    segment_id: str
    check_type: str      # "terminology" | "numbers" | "tags" | "empty" | "punctuation" | "consistency"
    severity: str        # "error" | "warning"
    message: str
    source_text: str
    target_text: str
    suggestion: str = ""


class QAEngine:
    """
    Post-translation QA engine.
    All 6 checks are enabled by default.
    Pass enabled_checks=[] to disable all, or a subset to enable specific ones.
    """

    CHECK_TERMINOLOGY  = "terminology"
    CHECK_NUMBERS      = "numbers"
    CHECK_TAGS         = "tags"
    CHECK_EMPTY        = "empty"
    CHECK_PUNCTUATION  = "punctuation"
    CHECK_CONSISTENCY  = "consistency"

    ALL_CHECKS = [
        CHECK_TERMINOLOGY,
        CHECK_NUMBERS,
        CHECK_TAGS,
        CHECK_EMPTY,
        CHECK_PUNCTUATION,
        CHECK_CONSISTENCY,
    ]

    def __init__(self, enabled_checks: Optional[List[str]] = None):
        self.enabled_checks = set(enabled_checks if enabled_checks is not None else self.ALL_CHECKS)

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    def run_all_checks(
        self,
        segments,
        tb_terms: Optional[Dict[int, List[Dict]]] = None,
    ) -> List[QAIssue]:
        """
        Run all enabled checks on translated segments.

        Args:
            segments : List[TranslationSegment] — source + target must be filled.
            tb_terms : Pre-fetched TB lookup result {seg_idx: [{source, target, is_forbidden}]}.
                       Pass None (or omit) to skip the terminology check.

        Returns:
            List[QAIssue] sorted by segment_index.
        """
        issues: List[QAIssue] = []

        for check in self.ALL_CHECKS:
            if check not in self.enabled_checks:
                continue

            if check == self.CHECK_TERMINOLOGY:
                if tb_terms is not None:
                    issues.extend(self._check_terminology(segments, tb_terms))
            elif check == self.CHECK_NUMBERS:
                issues.extend(self._check_numbers(segments))
            elif check == self.CHECK_TAGS:
                issues.extend(self._check_tags(segments))
            elif check == self.CHECK_EMPTY:
                issues.extend(self._check_empty(segments))
            elif check == self.CHECK_PUNCTUATION:
                issues.extend(self._check_punctuation(segments))
            elif check == self.CHECK_CONSISTENCY:
                issues.extend(self._check_consistency(segments))

        issues.sort(key=lambda x: x.segment_index)
        return issues

    # ─────────────────────────────────────────────────────────────────────────
    # Check 1 — Terminology
    # ─────────────────────────────────────────────────────────────────────────

    def _check_terminology(
        self,
        segments,
        tb_terms: Dict[int, List[Dict]],
    ) -> List[QAIssue]:
        """
        For each segment with TB hits:
        - Required term: target must contain the TB target term (case-insensitive substring).
        - Forbidden term: target must NOT contain the forbidden TB term.
        """
        issues: List[QAIssue] = []

        for seg_idx, hits in tb_terms.items():
            if seg_idx >= len(segments):
                continue
            seg = segments[seg_idx]
            if not seg.target:
                continue
            target_lower = seg.target.lower()

            for hit in hits:
                if hit.get("is_forbidden"):
                    if hit["target"].lower() in target_lower:
                        issues.append(QAIssue(
                            segment_index=seg_idx,
                            segment_id=seg.id,
                            check_type=self.CHECK_TERMINOLOGY,
                            severity="error",
                            message=f"Forbidden term used: \u2018{hit['target']}\u2019",
                            source_text=seg.source,
                            target_text=seg.target,
                            suggestion=f"Remove or replace \u2018{hit['target']}\u2019",
                        ))
                else:
                    if hit["target"].lower() not in target_lower:
                        issues.append(QAIssue(
                            segment_index=seg_idx,
                            segment_id=seg.id,
                            check_type=self.CHECK_TERMINOLOGY,
                            severity="warning",
                            message=(
                                f"TB term possibly missing: "
                                f"\u2018{hit['source']}\u2019 \u2192 \u2018{hit['target']}\u2019"
                            ),
                            source_text=seg.source,
                            target_text=seg.target,
                            suggestion=f"Consider using \u2018{hit['target']}\u2019 for \u2018{hit['source']}\u2019",
                        ))

        return issues

    # ─────────────────────────────────────────────────────────────────────────
    # Check 2 — Numbers
    # ─────────────────────────────────────────────────────────────────────────

    _NUMBER_RE = re.compile(r"\b\d[\d.,]*\b")

    def _check_numbers(self, segments) -> List[QAIssue]:
        """Every number token in source must appear verbatim in target."""
        issues: List[QAIssue] = []

        for idx, seg in enumerate(segments):
            if not seg.source or not seg.target:
                continue
            src_nums = set(self._NUMBER_RE.findall(seg.source))
            tgt_nums = set(self._NUMBER_RE.findall(seg.target))
            missing = src_nums - tgt_nums

            for num in sorted(missing):
                issues.append(QAIssue(
                    segment_index=idx,
                    segment_id=seg.id,
                    check_type=self.CHECK_NUMBERS,
                    severity="error",
                    message=f"Number missing in target: \u2018{num}\u2019",
                    source_text=seg.source,
                    target_text=seg.target,
                ))

        return issues

    # ─────────────────────────────────────────────────────────────────────────
    # Check 3 — Tags
    # ─────────────────────────────────────────────────────────────────────────

    _TAG_RE = re.compile(r"\{\{\d+\}\}")

    def _check_tags(self, segments) -> List[QAIssue]:
        """{{n}} placeholder set in source must exactly match target."""
        issues: List[QAIssue] = []

        for idx, seg in enumerate(segments):
            if not seg.source or not seg.target:
                continue
            src_tags = set(self._TAG_RE.findall(seg.source))
            tgt_tags = set(self._TAG_RE.findall(seg.target))

            if src_tags != tgt_tags:
                missing = src_tags - tgt_tags
                extra   = tgt_tags - src_tags
                parts = []
                if missing:
                    parts.append(f"missing: {', '.join(sorted(missing))}")
                if extra:
                    parts.append(f"extra: {', '.join(sorted(extra))}")
                issues.append(QAIssue(
                    segment_index=idx,
                    segment_id=seg.id,
                    check_type=self.CHECK_TAGS,
                    severity="error",
                    message=f"Tag mismatch — {'; '.join(parts)}",
                    source_text=seg.source,
                    target_text=seg.target,
                ))

        return issues

    # ─────────────────────────────────────────────────────────────────────────
    # Check 4 — Empty segments
    # ─────────────────────────────────────────────────────────────────────────

    def _check_empty(self, segments) -> List[QAIssue]:
        """Source has content but target is blank or whitespace-only."""
        issues: List[QAIssue] = []

        for idx, seg in enumerate(segments):
            if seg.source and seg.source.strip():
                if not (seg.target and seg.target.strip()):
                    issues.append(QAIssue(
                        segment_index=idx,
                        segment_id=seg.id,
                        check_type=self.CHECK_EMPTY,
                        severity="error",
                        message="Segment not translated (empty target)",
                        source_text=seg.source,
                        target_text=seg.target or "",
                    ))

        return issues

    # ─────────────────────────────────────────────────────────────────────────
    # Check 5 — Punctuation
    # ─────────────────────────────────────────────────────────────────────────

    _TERMINAL_RE = re.compile(r"[.!?:;,]$")

    def _check_punctuation(self, segments) -> List[QAIssue]:
        """Terminal punctuation character must match between source and target."""
        issues: List[QAIssue] = []

        for idx, seg in enumerate(segments):
            if not seg.source or not seg.target:
                continue
            src_end = seg.source.rstrip()[-1:] if seg.source.rstrip() else ""
            tgt_end = seg.target.rstrip()[-1:] if seg.target.rstrip() else ""
            src_has = bool(self._TERMINAL_RE.match(src_end))
            tgt_has = bool(self._TERMINAL_RE.match(tgt_end))

            if src_has and not tgt_has:
                issues.append(QAIssue(
                    segment_index=idx,
                    segment_id=seg.id,
                    check_type=self.CHECK_PUNCTUATION,
                    severity="warning",
                    message=f"Missing terminal punctuation (source ends with \u2018{src_end}\u2019)",
                    source_text=seg.source,
                    target_text=seg.target,
                    suggestion=f"Add \u2018{src_end}\u2019 at end of target",
                ))
            elif not src_has and tgt_has:
                issues.append(QAIssue(
                    segment_index=idx,
                    segment_id=seg.id,
                    check_type=self.CHECK_PUNCTUATION,
                    severity="warning",
                    message=f"Extra terminal punctuation in target (ends with \u2018{tgt_end}\u2019)",
                    source_text=seg.source,
                    target_text=seg.target,
                    suggestion=f"Remove \u2018{tgt_end}\u2019 from end of target",
                ))

        return issues

    # ─────────────────────────────────────────────────────────────────────────
    # Check 6 — Consistency
    # ─────────────────────────────────────────────────────────────────────────

    def _check_consistency(self, segments) -> List[QAIssue]:
        """
        Same source text (case-insensitive, stripped) must always produce
        the same translation. Flags all occurrences when multiple targets exist.
        """
        issues: List[QAIssue] = []
        src_map: Dict[str, List[Tuple[int, str, str]]] = {}

        for idx, seg in enumerate(segments):
            if not seg.source or not seg.target:
                continue
            key = seg.source.strip().lower()
            src_map.setdefault(key, []).append((idx, seg.id, seg.target.strip()))

        for key, occurrences in src_map.items():
            if len(occurrences) < 2:
                continue
            unique_targets = {t for _, _, t in occurrences}
            if len(unique_targets) <= 1:
                continue

            variants = " / ".join(f"\u2018{t[:35]}\u2019" for t in sorted(unique_targets))
            for idx, seg_id, target in occurrences:
                seg = segments[idx]
                issues.append(QAIssue(
                    segment_index=idx,
                    segment_id=seg_id,
                    check_type=self.CHECK_CONSISTENCY,
                    severity="warning",
                    message=f"Inconsistent translation: {variants}",
                    source_text=seg.source,
                    target_text=target,
                ))

        return issues
