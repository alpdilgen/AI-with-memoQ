"""
QA Engine - Post-translation quality assurance checks.
5 checks: terminology, untranslated, tags, punctuation, consistency.
All checks are enabled by default.
"""

import re
import logging
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class QAIssue:
    """A single QA issue found in a segment."""
    segment_index: int
    segment_id: str
    check_type: str      # "terminology" | "untranslated" | "tags" | "punctuation" | "consistency"
    severity: str        # "error" | "warning"
    message: str
    source_text: str
    target_text: str
    suggestion: str = ""


class QAEngine:
    """
    Post-translation QA engine.
    All 5 checks are enabled by default.
    Pass enabled_checks=[] to disable all, or a subset to enable specific ones.
    """

    CHECK_TERMINOLOGY  = "terminology"
    CHECK_UNTRANSLATED = "untranslated"
    CHECK_TAGS         = "tags"
    CHECK_PUNCTUATION  = "punctuation"
    CHECK_CONSISTENCY  = "consistency"

    ALL_CHECKS = [
        CHECK_TERMINOLOGY,
        CHECK_UNTRANSLATED,
        CHECK_TAGS,
        CHECK_PUNCTUATION,
        CHECK_CONSISTENCY,
    ]

    # Compiled patterns
    _TAG_RE       = re.compile(r"\{\{\d+\}\}")
    _TERMINAL_RE  = re.compile(r"[.!?:;,]$")

    def __init__(self, enabled_checks: Optional[List[str]] = None):
        self.enabled_checks = set(enabled_checks if enabled_checks is not None else self.ALL_CHECKS)

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _strip_placeholders(self, text: str) -> str:
        """Remove {{n}} placeholders before analysis."""
        return self._TAG_RE.sub(" ", text)

    def _terminal_char(self, text: str) -> str:
        """Return the last non-whitespace, non-placeholder character of text."""
        clean = self._strip_placeholders(text).rstrip()
        return clean[-1:] if clean else ""

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
            elif check == self.CHECK_UNTRANSLATED:
                issues.extend(self._check_untranslated(segments))
            elif check == self.CHECK_TAGS:
                issues.extend(self._check_tags(segments))
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
        - Forbidden term: target must NOT contain the forbidden TB target term.
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
    # Check 2 — Untranslated segments
    # ─────────────────────────────────────────────────────────────────────────

    def _check_untranslated(self, segments) -> List[QAIssue]:
        """
        Two sub-cases:
        (a) Source has content but target is blank/whitespace  → error
        (b) Target text is identical to source text (not translated) → warning
        Comparison for (b) is case-insensitive and strips placeholders.
        """
        issues: List[QAIssue] = []

        for idx, seg in enumerate(segments):
            src = seg.source or ""
            tgt = seg.target or ""

            if not src.strip():
                continue  # empty source segment — skip

            # (a) Empty target
            if not tgt.strip():
                issues.append(QAIssue(
                    segment_index=idx,
                    segment_id=seg.id,
                    check_type=self.CHECK_UNTRANSLATED,
                    severity="error",
                    message="Segment not translated (empty target)",
                    source_text=src,
                    target_text=tgt,
                ))
                continue  # no need to check (b) if already empty

            # (b) Source == Target (identical content, likely untouched)
            src_norm = self._strip_placeholders(src).strip().lower()
            tgt_norm = self._strip_placeholders(tgt).strip().lower()
            if src_norm and tgt_norm and src_norm == tgt_norm:
                # Skip segments with no translatable text: numbers, codes, symbols.
                # If the source has no alphabetic characters it is expected to be
                # unchanged in the target (e.g. "100", "2.5%", "ABC-123").
                if not re.search(r"[a-zA-Z\u00C0-\u024F\u0400-\u04FF]", src_norm):
                    pass  # non-translatable content — identical target is correct
                else:
                    issues.append(QAIssue(
                        segment_index=idx,
                        segment_id=seg.id,
                        check_type=self.CHECK_UNTRANSLATED,
                        severity="warning",
                        message="Target is identical to source (possibly not translated)",
                        source_text=src,
                        target_text=tgt,
                    ))

        return issues

    # ─────────────────────────────────────────────────────────────────────────
    # Check 3 — Tags
    # ─────────────────────────────────────────────────────────────────────────

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
    # Check 4 — Punctuation
    # ─────────────────────────────────────────────────────────────────────────

    def _check_punctuation(self, segments) -> List[QAIssue]:
        """
        Terminal punctuation must match between source and target.
        Placeholders ({{n}}) are stripped before checking so trailing tags
        do not mask or fake punctuation.
        """
        issues: List[QAIssue] = []

        for idx, seg in enumerate(segments):
            if not seg.source or not seg.target:
                continue

            src_end = self._terminal_char(seg.source)
            tgt_end = self._terminal_char(seg.target)

            src_has = bool(src_end and self._TERMINAL_RE.match(src_end))
            tgt_has = bool(tgt_end and self._TERMINAL_RE.match(tgt_end))

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
            elif src_has and tgt_has and src_end != tgt_end:
                issues.append(QAIssue(
                    segment_index=idx,
                    segment_id=seg.id,
                    check_type=self.CHECK_PUNCTUATION,
                    severity="warning",
                    message=f"Terminal punctuation mismatch: source \u2018{src_end}\u2019 vs target \u2018{tgt_end}\u2019",
                    source_text=seg.source,
                    target_text=seg.target,
                    suggestion=f"Change target ending to \u2018{src_end}\u2019",
                ))

        return issues

    # ─────────────────────────────────────────────────────────────────────────
    # Check 5 — Consistency
    # ─────────────────────────────────────────────────────────────────────────

    def _check_consistency(self, segments) -> List[QAIssue]:
        """
        Same source text (case-insensitive, stripped, placeholders normalised)
        must always produce the same translation.
        Flags all occurrences when multiple targets exist.
        """
        issues: List[QAIssue] = []
        src_map: Dict[str, List[Tuple[int, str, str]]] = {}

        for idx, seg in enumerate(segments):
            if not seg.source or not seg.target:
                continue
            # Normalise: strip, lowercase, collapse placeholder numbers
            # so {{1}} and {{2}} are treated the same (only structure matters)
            key = self._TAG_RE.sub("{{?}}", seg.source.strip().lower())
            src_map.setdefault(key, []).append((idx, seg.id, seg.target.strip()))

        for key, occurrences in src_map.items():
            if len(occurrences) < 2:
                continue
            unique_targets = {t for _, _, t in occurrences}
            if len(unique_targets) <= 1:
                continue

            variants = " / ".join(f"\u2018{t[:35]}\u2019" for t in sorted(unique_targets))
            for idx, seg_id,