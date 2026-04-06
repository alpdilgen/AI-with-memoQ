# memoQ-AI Repository — Fix & Improvement Instructions for Claude Code

**Date:** 2026-04-06
**Repository:** memoQ-AI-main (Streamlit translation platform with memoQ Server integration)
**Scope:** Fix all identified bugs, improve performance, ensure memoQ Resource API compliance

> **IMPORTANT:** Do NOT touch frontend/ folder, backend/ folder, docker files, or supabase/. Only modify the Streamlit app and its services/utils/models.

---

## REFERENCE: memoQ Server Resources API

Base URL pattern: `https://{server}:{port}/{instance}/memoqserverhttpapi/v1/`

### Authentication
- `POST /auth/login` — Body: `{"UserName": "x", "Password": "y", "LoginMode": 0}` → Response: `{"Name": "x", "Sid": "guid", "AccessToken": "token"}`
- Token passed as query param `?authToken=xxx` or header `Authorization: MQS-API xxx`
- Session expires after inactivity (NOT fixed time). Error: `{"ErrorCode": "InvalidOrExpiredToken"}`

### TM Lookup (POST /tms/{tmGuid}/lookupsegments)
Request:
```json
{
  "Segments": [
    {"Segment": "<seg>text1</seg>"},
    {"Segment": "<seg>text2</seg>"}
  ],
  "Options": {"MatchThreshold": 70, "OnlyBest": false}
}
```
Response — **Result is an ARRAY, one entry per input segment:**
```json
{
  "Result": [
    {
      "TMHits": [
        {
          "MatchRate": 95,
          "TransUnit": {
            "SourceSegment": "<seg>source text</seg>",
            "TargetSegment": "<seg>target text</seg>",
            "Created": "2024-01-01T00:00:00Z",
            "Creator": "user",
            "Modified": "2024-01-01T00:00:00Z",
            "Modifier": "user",
            "Client": "", "Domain": "", "Project": "", "Subject": "",
            "Document": "", "Key": 0, "ContextID": "",
            "CustomMetas": []
          }
        }
      ]
    },
    {
      "TMHits": []
    }
  ]
}
```

### TB Lookup (POST /tbs/{tbGuid}/lookupterms)
Request:
```json
{
  "SourceLanguage": "eng",
  "TargetLanguage": "tur",
  "Segments": ["<seg>source text</seg>"]
}
```
Response — **Structure is completely different from TM. NO TransUnit, NO SourceTerm/TargetTerm at top level:**
```json
[
  {
    "TBHits": [
      {
        "Entry": {
          "Id": 123,
          "Languages": [
            {
              "Language": "eng",
              "TermItems": [{"Text": "source term"}]
            },
            {
              "Language": "tur",
              "TermItems": [{"Text": "hedef terim"}]
            }
          ]
        },
        "MatchRate": 100,
        "LengthInSegment": 5,
        "StartPosInSegment": 0
      }
    ]
  }
]
```

### Concordance Search (POST /tms/{tmGuid}/concordance)
Request:
```json
{
  "SearchExpression": ["search term"],
  "Options": {"ResultsLimit": 64, "Ascending": false, "Column": 3}
}
```
Response:
```json
{
  "ConcResult": [
    {
      "TMEntry": {
        "SourceSegment": "<seg>full segment containing term</seg>",
        "TargetSegment": "<seg>translation</seg>"
      },
      "ConcordanceTextRanges": [{"Start": 0, "Length": 7}]
    }
  ],
  "TotalConcResult": 1
}
```

### Error Codes
| HTTP | ErrorCode | Meaning |
|------|-----------|---------|
| 401 | AuthenticationFailed | Bad credentials |
| 401 | InvalidOrExpiredToken | Token expired / invalid |
| 401 | TooFrequentLogin | Rate limited |
| 401 | NoLicenseAvailable | No translator license |
| 403 | Unauthorized | No permission for resource |
| 404 | ResourceNotFound | TM/TB not found |
| 409 | OptimisticConcurrencyError | Concurrent modification |

---

## FIX INSTRUCTIONS

### FIX-1: Rewrite `normalize_memoq_tm_response()` to handle ALL segments [CRITICAL]

**File:** `services/memoq_server_client.py`
**Lines:** 18-90
**Problem:** Only processes `result_list[0]` — ignores all other segments in the batch.
**Fix:** Iterate through ALL items in `Result` array. Return a dict keyed by segment index.

```python
def normalize_memoq_tm_response(memoq_response: Dict, match_threshold: int = 70) -> Dict[int, List]:
    """
    Convert memoQ TM API response to standard TMMatch objects.

    Returns:
        Dict[int, List[TMMatch]]: {segment_index: [TMMatch objects sorted by similarity desc]}
    """
    from models.entities import TMMatch

    results_by_segment = {}

    try:
        result_list = memoq_response.get('Result', [])
        if not result_list:
            return {}

        for seg_idx, segment_result in enumerate(result_list):
            matches = []
            tm_hits = segment_result.get('TMHits', [])

            for hit in tm_hits:
                match_rate = hit.get('MatchRate', 0)
                if match_rate < match_threshold:
                    continue

                trans_unit = hit.get('TransUnit', {})
                if not trans_unit:
                    continue

                source_seg = trans_unit.get('SourceSegment', '')
                target_seg = trans_unit.get('TargetSegment', '')
                if not source_seg or not target_seg:
                    continue

                # Clean XML tags: <seg>text</seg> → text
                source_text = re.sub(r'</?seg>', '', source_seg).strip()
                target_text = re.sub(r'</?seg>', '', target_seg).strip()
                if not source_text or not target_text:
                    continue

                match_type = "EXACT" if match_rate >= 100 else "FUZZY"

                try:
                    match = TMMatch(
                        source_text=source_text,
                        target_text=target_text,
                        similarity=match_rate,
                        match_type=match_type,
                        metadata={
                            'creator': trans_unit.get('Creator', ''),
                            'modified': trans_unit.get('Modified', ''),
                            'document': trans_unit.get('Document', ''),
                            'domain': trans_unit.get('Domain', ''),
                            'project': trans_unit.get('Project', ''),
                        }
                    )
                    matches.append(match)
                except Exception as e:
                    logger.warning(f"Segment {seg_idx}: Invalid TMMatch: {e}")
                    continue

            if matches:
                matches.sort(key=lambda x: x.similarity, reverse=True)
                results_by_segment[seg_idx] = matches[:10]

    except Exception as e:
        logger.error(f"Error normalizing memoQ TM response: {e}")
        return {}

    return results_by_segment
```

### FIX-2: Completely rewrite `normalize_memoq_tb_response()` for correct API format [CRITICAL]

**File:** `services/memoq_server_client.py`
**Lines:** 93-150
**Problem:** Expects `TransUnit.SourceTerm`/`TransUnit.TargetTerm` but memoQ API returns `Entry.Languages[].TermItems[].Text` structure.
**Fix:** Parse the real memoQ TB response format.

```python
def normalize_memoq_tb_response(memoq_response, src_lang: str = "eng", tgt_lang: str = "tur") -> List:
    """
    Convert memoQ TB API response to standard TermMatch objects.

    The memoQ TB lookup response is a LIST (not dict), where each item has TBHits.
    Each TBHit has an Entry with Languages array containing TermItems.

    Args:
        memoq_response: Raw response from memoQ Server TB lookup (list or dict)
        src_lang: Source language code (e.g., 'eng')
        tgt_lang: Target language code (e.g., 'tur')

    Returns:
        List of TermMatch objects
    """
    from models.entities import TermMatch

    terms = []
    seen = set()  # Deduplicate

    try:
        # Response can be a list (direct) or dict with 'Result' key
        if isinstance(memoq_response, list):
            result_list = memoq_response
        elif isinstance(memoq_response, dict):
            result_list = memoq_response.get('Result', memoq_response.get('result', []))
            if not isinstance(result_list, list):
                result_list = [result_list]
        else:
            logger.warning(f"Unexpected TB response type: {type(memoq_response)}")
            return []

        for segment_result in result_list:
            if not isinstance(segment_result, dict):
                continue

            tb_hits = segment_result.get('TBHits', [])

            for hit in tb_hits:
                entry = hit.get('Entry', {})
                if not entry:
                    continue

                languages = entry.get('Languages', [])
                if not languages:
                    continue

                # Find source and target language entries
                source_terms = []
                target_terms = []

                for lang_entry in languages:
                    lang_code = lang_entry.get('Language', '').lower()
                    term_items = lang_entry.get('TermItems', [])

                    for term_item in term_items:
                        term_text = term_item.get('Text', '').strip()
                        if not term_text:
                            continue

                        is_forbidden = term_item.get('IsForbidden', False)
                        if is_forbidden:
                            continue

                        if lang_code == src_lang.lower() or lang_code.startswith(src_lang.lower()[:3]):
                            source_terms.append(term_text)
                        elif lang_code == tgt_lang.lower() or lang_code.startswith(tgt_lang.lower()[:3]):
                            target_terms.append(term_text)

                # Create TermMatch for each source-target pair
                for src_term in source_terms:
                    for tgt_term in target_terms:
                        pair_key = (src_term.lower(), tgt_term.lower())
                        if pair_key in seen:
                            continue
                        seen.add(pair_key)

                        try:
                            term = TermMatch(
                                source=src_term,
                                target=tgt_term,
                                source_language=src_lang,
                                target_language=tgt_lang
                            )
                            terms.append(term)
                            logger.debug(f"TB term: {src_term} = {tgt_term}")
                        except Exception as e:
                            logger.warning(f"Invalid TermMatch: {e}")
                            continue

    except Exception as e:
        logger.error(f"Error normalizing memoQ TB response: {e}")
        return []

    return terms
```

### FIX-3: Rewrite `lookup_segments()` to return per-segment results [CRITICAL]

**File:** `services/memoq_server_client.py`
**Lines:** 327-419
**Problem:** Returns `{0: all_matches}` — all results under index 0. Must return `{seg_index: [matches]}`.
**Fix:**

Replace lines 390-419 (the `if result and isinstance(result, dict):` block) with:

```python
            if result and isinstance(result, dict):
                result_list = result.get("Result", [])
                logger.info(f"TM lookup Result count: {len(result_list) if result_list else 0}")

                if result_list:
                    # Normalize ALL segments — returns {seg_idx: [TMMatch]}
                    results_by_segment = normalize_memoq_tm_response(
                        result,
                        match_threshold=match_threshold
                    )

                    for idx, matches in results_by_segment.items():
                        logger.info(f"Segment {idx}: {len(matches)} matches, best={matches[0].similarity}%")

                    return results_by_segment
                else:
                    logger.warning("TM lookup returned empty Result")
                    return {}
            else:
                logger.warning(f"TM lookup unexpected format: {type(result)}")
                return {}
```

### FIX-4: Rewrite `lookup_terms()` to pass correct language params [CRITICAL]

**File:** `services/memoq_server_client.py`
**Lines:** 465-534
**Problem:** Response normalization uses wrong function signature. Also `lookup_terms` needs to pass src/tgt lang to normalizer.
**Fix:** Update the normalization call at lines 520-524:

```python
                    normalized_terms = normalize_memoq_tb_response(
                        result,
                        src_lang=src_lang,
                        tgt_lang=tgt_lang
                    )
```

Also: The response from `/tbs/{tbGuid}/lookupterms` may be a list directly (not wrapped in `{"Result": [...]}`). The new `normalize_memoq_tb_response` handles both formats.

### FIX-5: Add token auto-recovery on InvalidOrExpiredToken [CRITICAL]

**File:** `services/memoq_server_client.py`
**Method:** `_make_request()` (lines 232-297)
**Problem:** No retry on expired token. Long-running jobs fail mid-way.
**Fix:** Add retry logic inside `_make_request()`. After the `except requests.exceptions.HTTPError` block, detect token expiry and retry once:

```python
        except requests.exceptions.HTTPError as e:
            try:
                error_data = response.json()
                error_code = error_data.get("ErrorCode", "Unknown")
                error_msg = error_data.get("Message", "")

                # Auto-recover on expired token
                if error_code == "InvalidOrExpiredToken":
                    logger.warning("Token expired, attempting re-login...")
                    self.token = None
                    self.token_expiry = None
                    if self.login():
                        # Retry the request once with new token
                        request_params["authToken"] = self.token
                        if method == "GET":
                            response = requests.get(url, params=request_params, headers=headers,
                                                   verify=self.verify_ssl, timeout=self.timeout)
                        else:
                            response = requests.post(url, json=data, params=request_params, headers=headers,
                                                    verify=self.verify_ssl, timeout=self.timeout)
                        response.raise_for_status()
                        return response.json()
                    else:
                        raise Exception("Re-authentication failed after token expiry")

                raise Exception(f"HTTP {response.status_code}: {error_code}: {error_msg}")
            except ValueError:
                raise Exception(f"HTTP {response.status_code}: {str(e)}")
```

### FIX-6: Rewrite app.py segment analysis loop for BATCH processing [CRITICAL]

**File:** `app.py`
**Lines:** 717-857 (the entire segment analysis loop)
**Problem:** Sends 1 API request per segment per TM. Must batch segments.
**Fix:** Replace the entire segment-by-segment loop with batch logic.

The new approach:
1. Collect ALL segments that need memoQ lookup
2. Send them in batches of 50 to each TM (one API call per batch per TM)
3. Process results

```python
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
        segments_for_memoq = [s for s in segments_needing_tm if s.id not in local_tm_matched_ids or s.id in tm_context]
        # Actually, segments that got bypassed by local TM should NOT go to memoQ
        segments_for_memoq = [s for s in segments_needing_tm if s.id not in {s2.id for s2 in bypass_segments}]

        if memoq_client and memoq_tm_guids and segments_for_memoq:
            BATCH_SIZE = 50

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

                    progress = 0.3 + (batch_start + len(batch)) / len(remaining) * 0.3
                    analysis_progress.progress(min(progress, 0.6))

        # STEP D: Determine which segments go to LLM
        bypassed_ids = {s.id for s in bypass_segments}
        for seg in segments_needing_tm:
            if seg.id not in bypassed_ids:
                llm_segments.append(seg)
                if seg.id not in match_scores:
                    match_scores[seg.id] = 0

        # STEP E: memoQ TB batch lookup
        if memoq_client and memoq_tb_guids:
            all_segment_sources = [s.source for s in segments_needing_tm]

            for tb_guid in memoq_tb_guids:
                for batch_start in range(0, len(all_segment_sources), BATCH_SIZE):
                    batch_sources = all_segment_sources[batch_start:batch_start + BATCH_SIZE]
                    batch_segs = segments_needing_tm[batch_start:batch_start + BATCH_SIZE]

                    try:
                        tb_results = memoq_client.lookup_terms(
                            tb_guid, batch_sources,
                            src_lang=src_code, tgt_lang=tgt_code
                        )
                        if tb_results:
                            for seg in batch_segs:
                                # TB results apply to all segments
                                tb_context[seg.id] = tb_results
                    except Exception as e:
                        logger.log(f"memoQ TB batch error: {e}")

            analysis_progress.progress(0.8)

        # Local TB matching
        if tb_matcher:
            for seg in segments_needing_tm:
                tb_matches = tb_matcher.extract_matches(seg.source)
                if tb_matches:
                    existing = tb_context.get(seg.id, [])
                    tb_context[seg.id] = existing + tb_matches

        analysis_progress.progress(1.0)
```

**IMPORTANT:** This replaces the ENTIRE analysis loop (lines 707-858 approximately). The rest of the code (batch LLM translation starting from line 883) stays the same but needs adjustment: remove the `elif not segment_matched` blocks.

### FIX-7: Fix analysis_screen.py TM analysis to use batch lookup [HIGH]

**File:** `app.py`
**Lines:** 1148-1265 (the analysis button handler)
**Problem:** Same segment-by-segment API calls. Must use batch lookup.
**Fix:** Replace the per-segment loop with batch approach identical to FIX-6 pattern. Collect all segments, send in batches of 50, categorize results.

### FIX-8: Fix word count calculation [HIGH]

**File:** `app.py`
**Line:** 1179
**Current:** `word_count = len(seg.source.split())`
**Fix:**
```python
# Remove tags before counting words
clean_text = re.sub(r'<[^>]+>|\{\{\d+\}\}', '', seg.source).strip()
word_count = len(clean_text.split()) if clean_text else 0
```
Apply same fix wherever word count is calculated.

### FIX-9: Add QAError dataclass to entities.py [MEDIUM]

**File:** `models/entities.py`
**Problem:** `qa_error_fixer.py` imports `QAError` but it doesn't exist.
**Fix:** Add at end of entities.py:

```python
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
```

### FIX-10: Extract language code mapping to config.py [MEDIUM]

**File:** `app.py` (lines 100-148) and `config.py`
**Problem:** `base_lang_map` dictionary is repeated 4 times.
**Fix:** Add to `config.py`:

```python
# ISO 639-1 to memoQ 3-letter language code mapping
ISO_TO_MEMOQ_LANG = {
    'en': 'eng', 'tr': 'tur', 'de': 'ger', 'fr': 'fre', 'es': 'spa',
    'it': 'ita', 'pt': 'por', 'pl': 'pol', 'ru': 'rus', 'ja': 'jpn',
    'zh': 'zho', 'ar': 'ara', 'ko': 'kor', 'nl': 'dut', 'sv': 'swe',
    'no': 'nor', 'da': 'dan', 'fi': 'fin', 'el': 'gre', 'he': 'heb',
    'th': 'tha', 'vi': 'vie', 'bg': 'bul', 'ro': 'rum', 'cs': 'cze',
    'sk': 'slo', 'uk': 'ukr', 'et': 'est', 'lv': 'lav', 'lt': 'lit',
    'hu': 'hun', 'hr': 'hrv', 'sl': 'slv', 'mt': 'mlt', 'ga': 'gle',
    'af': 'afr', 'bn': 'ben', 'hi': 'hin',
}

def convert_detected_lang(detected_code: str) -> str:
    """Convert auto-detected ISO code to memoQ 3-letter code."""
    if not detected_code:
        return detected_code
    parts = detected_code.split('-')
    if len(parts) == 2:
        base = ISO_TO_MEMOQ_LANG.get(parts[0], parts[0])
        return f"{base}-{parts[1].upper()}"
    return ISO_TO_MEMOQ_LANG.get(detected_code, detected_code)
```

Then in `app.py`, replace all 4 blocks (lines 100-148) with:
```python
    detected_src = config.convert_detected_lang(detected_src) if detected_src else None
    detected_tgt = config.convert_detected_lang(detected_tgt) if detected_tgt else None
```

### FIX-11: Delete tm_matcher_single.py [MEDIUM]

**File:** `services/tm_matcher_single.py`
**Action:** Delete this file. It is an exact duplicate of `tm_matcher.py`. Verify no imports reference it first:
```bash
grep -r "tm_matcher_single" . --include="*.py"
```

### FIX-12: Fix analysis_screen.py unused parameters [MEDIUM]

**File:** `analysis_screen.py`
**Line:** 56
**Current:** `def show_analysis_screen(xliff_file, selected_tms_count, analysis_results):`
**Fix:** Remove unused params or use them:
```python
def show_analysis_screen(analysis_results):
```
Update the call in `app.py` line 1269 accordingly.

### FIX-13: Fix services/__init__.py exports [MEDIUM]

**File:** `services/__init__.py`
**Fix:** Update to export all services:

```python
from services.memoq_server_client import MemoQServerClient, normalize_memoq_tm_response, normalize_memoq_tb_response
from services.prompt_builder import PromptBuilder
from services.ai_translator import AITranslator
from services.tm_matcher import TMatcher
from services.tb_matcher import TBMatcher
from services.doc_analyzer import DocumentAnalyzer, PromptGenerator
from services.embedding_matcher import EmbeddingMatcher
from services.caching import CacheManager
from services.qa_error_fixer import QAErrorFixer
from services.memoq_ui import MemoQUI

__all__ = [
    'MemoQServerClient', 'normalize_memoq_tm_response', 'normalize_memoq_tb_response',
    'PromptBuilder', 'AITranslator', 'TMatcher', 'TBMatcher',
    'DocumentAnalyzer', 'PromptGenerator', 'EmbeddingMatcher',
    'CacheManager', 'QAErrorFixer', 'MemoQUI',
]
```

### FIX-14: Add proper cost estimation with real token counts [MEDIUM]

**File:** `analysis_screen.py`, function `calculate_cost_estimate()`
**Problem:** Fixed 100 tokens per segment is unrealistic.
**Fix:** Estimate based on word count:
```python
# Approximate: 1 word ≈ 1.3 tokens for English, more for other languages
tokens_per_word = 1.5  # Conservative estimate
```

### FIX-15: Remove bare except clauses [LOW]

**Files:** `services/memoq_ui.py`, `app.py`
**Fix:** Replace all `except:` with `except Exception as e:` and add logging.

### FIX-16: Remove unused imports [LOW]

**Files:**
- `services/ai_translator.py`: Remove `import time`
- `services/caching.py`: Remove `import time`

### FIX-17: Update config.py with current models [LOW]

**File:** `config.py`
**Lines:** 52-55
**Fix:**
```python
OPENAI_MODELS = [
    'gpt-4o',
    'gpt-4o-mini',
    'gpt-4-turbo',
]
```

---

## EXECUTION ORDER

1. **FIX-9** first (add QAError to entities.py — other fixes depend on imports working)
2. **FIX-1, FIX-2, FIX-3, FIX-4, FIX-5** together (memoq_server_client.py rewrite)
3. **FIX-6** (app.py batch processing — the biggest change)
4. **FIX-7** (analysis screen batch)
5. **FIX-8** (word count)
6. **FIX-10** (language code DRY)
7. **FIX-11, FIX-12, FIX-13** (cleanup)
8. **FIX-14, FIX-15, FIX-16, FIX-17** (improvements)

---

## TESTING CHECKLIST

After all fixes, verify:

1. [ ] `python -c "from models.entities import QAError; print('OK')"` — no ImportError
2. [ ] `python -c "from services.memoq_server_client import MemoQServerClient; print('OK')"` — imports work
3. [ ] `python -c "from services.qa_error_fixer import QAErrorFixer; print('OK')"` — imports work
4. [ ] `streamlit run app.py` — app starts without errors
5. [ ] Connect to memoQ Server — login succeeds
6. [ ] Load TM list — shows TMs with metadata
7. [ ] Load TB list — shows TBs
8. [ ] Upload XLIFF + select TMs → Analyze → match distribution shows correctly
9. [ ] Word count in analysis is accurate (no tag inflation)
10. [ ] Start translation → batch TM lookup works (check logs: should see "50 segments" per request, not "1 segment")
11. [ ] TB matches appear in prompt context
12. [ ] Bypass segments get TM translation correctly
13. [ ] LLM segments get proper context
14. [ ] Downloaded XLIFF has correct memoQ metadata
15. [ ] Long-running job survives token refresh
