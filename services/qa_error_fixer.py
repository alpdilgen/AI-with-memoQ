import re
from models.entities import QAError, TranslationSegment
from typing import List, Dict

class QAErrorFixer:
    """
    Automated QA Fixer.
    Phase 1: Mechanical (Tags, Spaces, Punctuation)
    Phase 2: Terminology/TM (Context - placeholder for future logic)
    """
    
    def fix_segment(self, segment: TranslationSegment) -> List[QAError]:
        errors = []
        original_target = segment.target
        current_target = segment.target
        
        # --- Rule 1: Multiple Spaces (MemoQ 3050) ---
        if re.search(r' {2,}', current_target):
            fixed = re.sub(r' +', ' ', current_target)
            if fixed != current_target:
                errors.append(QAError(
                    code=3050, segment_id=segment.id, 
                    description="Multiple spaces found", 
                    status="fixed", original_target=current_target, fixed_target=fixed
                ))
                current_target = fixed

        # --- Rule 2: Trailing Space (MemoQ 3110) ---
        if current_target.strip() != current_target:
            fixed = current_target.strip()
            errors.append(QAError(
                code=3110, segment_id=segment.id,
                description="Trailing whitespace",
                status="fixed", original_target=current_target, fixed_target=fixed
            ))
            current_target = fixed

        # --- Rule 3: Missing Numbers (MemoQ 3062) ---
        src_nums = set(re.findall(r'\d+', segment.source))
        tgt_nums = set(re.findall(r'\d+', current_target))
        missing_nums = src_nums - tgt_nums
        if missing_nums:
            # Simple fix: Append missing numbers (Naive, but effective for lists)
            # In production, this needs smarter placement logic
            to_add = " ".join(missing_nums)
            fixed = current_target + f" {to_add}" 
            errors.append(QAError(
                code=3062, segment_id=segment.id,
                description=f"Missing numbers: {missing_nums}",
                status="fixed", original_target=current_target, fixed_target=fixed
            ))
            current_target = fixed

        # --- Rule 4: Punctuation Mismatch (End) (MemoQ 3020) ---
        if segment.source and current_target:
            src_end = segment.source.rstrip()[-1]
            tgt_end = current_target.rstrip()[-1]
            if src_end in ".!?:" and src_end != tgt_end:
                fixed = current_target.rstrip() + src_end
                errors.append(QAError(
                    code=3020, segment_id=segment.id,
                    description=f"End punctuation mismatch. Exp: {src_end}, Got: {tgt_end}",
                    status="fixed", original_target=current_target, fixed_target=fixed
                ))
                current_target = fixed
        
        # Apply final fix
        segment.target = current_target
        return errors

    def run_qa_batch(self, segments: List[TranslationSegment]):
        all_errors = []
        for seg in segments:
            seg_errors = self.fix_segment(seg)
            all_errors.extend(seg_errors)
        return all_errors