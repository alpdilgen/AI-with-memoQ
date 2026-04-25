"""
memoQ Server REST API Client
Handles communication with memoQ Server for TM and TB operations
"""

import requests
import logging
import re
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
from enum import Enum

logger = logging.getLogger(__name__)


# ===== NORMALIZATION FUNCTIONS =====

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


def normalize_memoq_tb_response(memoq_response, src_lang: str = "eng", tgt_lang: str = "tur") -> List:
    """
    Convert memoQ TB API response to standard TermMatch objects.

    The memoQ TB lookupterms response structure:
        {"Result": [{"TBHits": [[hit, ...], [hit, ...], ...]}, ...]}
    TBHits is a 2D array (TBHit[][]) per the memoQ API spec: outer index is the term
    position matched within the segment, inner index is the matching TB entry.
    Each TBHit contains SourceTerm and TargetTerm directly, plus a full Entry.

    Args:
        memoq_response: Raw response from memoQ Server TB lookup
        src_lang: Source language code (e.g., 'eng')
        tgt_lang: Target language code (e.g., 'tur')

    Returns:
        List of TermMatch objects (forbidden terms excluded)
    """
    from models.entities import TermMatch

    terms = []
    seen = set()

    try:
        # Response is always {"Result": [...]} per API spec
        if isinstance(memoq_response, dict):
            result_list = memoq_response.get('Result', [])
        elif isinstance(memoq_response, list):
            result_list = memoq_response  # Fallback for non-standard response
        else:
            logger.warning(f"Unexpected TB response type: {type(memoq_response)}")
            return []

        logger.debug(f"TB normalize: {len(result_list)} segment results")

        for segment_result in result_list:
            if not isinstance(segment_result, dict):
                continue

            # TBHits is TBHit[][] — a 2D array per the memoQ API spec
            tb_hits_2d = segment_result.get('TBHits', [])
            if not isinstance(tb_hits_2d, list):
                continue

            for inner_hits in tb_hits_2d:
                # Each outer element is an array of hits for one matched term position
                if not isinstance(inner_hits, list):
                    inner_hits = [inner_hits] if isinstance(inner_hits, dict) else []

                for hit in inner_hits:
                    if not isinstance(hit, dict):
                        continue

                    # SourceTerm and TargetTerm are provided directly in the hit
                    source_term = hit.get('SourceTerm', '').strip()
                    target_term = hit.get('TargetTerm', '').strip()

                    if not source_term or not target_term:
                        continue

                    # Check IsForbidden on the specific target TermItem via Entry
                    is_forbidden = False
                    entry = hit.get('Entry', {})
                    if isinstance(entry, dict):
                        for lang_entry in entry.get('Languages', []):
                            if not isinstance(lang_entry, dict):
                                continue
                            lang_code = lang_entry.get('Language', '').lower()
                            if not lang_code.startswith(tgt_lang.lower()[:3]):
                                continue
                            for term_item in lang_entry.get('TermItems', []):
                                if not isinstance(term_item, dict):
                                    continue
                                if term_item.get('Text', '').strip() == target_term:
                                    is_forbidden = term_item.get('IsForbidden', False)
                                    break
                    if is_forbidden:
                        logger.debug(f"TB: skipping forbidden term: {source_term} → {target_term}")
                        continue

                    pair_key = (source_term.lower(), target_term.lower())
                    if pair_key in seen:
                        continue
                    seen.add(pair_key)

                    try:
                        term = TermMatch(
                            source=source_term,
                            target=target_term,
                            source_language=src_lang,
                            target_language=tgt_lang
                        )
                        terms.append(term)
                        logger.debug(f"TB term: {source_term} → {target_term}")
                    except Exception as e:
                        logger.warning(f"Invalid TermMatch: {e}")

    except Exception as e:
        logger.error(f"Error normalizing memoQ TB response: {e}")
        return []

    logger.debug(f"TB normalize: returning {len(terms)} terms")
    return terms


class MemoQServerClient:
    """
    REST API client for memoQ Server
    Handles Authentication, TM, and TB operations
    """
    
    def __init__(
        self,
        server_url: str,
        username: str,
        password: str,
        verify_ssl: bool = False,
        timeout: int = 30
    ):
        """
        Initialize memoQ Server connection
        
        Args:
            server_url: Base URL (e.g., https://mirage.memoq.com:8091/adaturkey)
            username: memoQ username
            password: memoQ password
            verify_ssl: SSL certificate verification
            timeout: Request timeout
        """
        self.server_url = server_url.rstrip('/')
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self.base_path = "/memoqserverhttpapi/v1"
        
        self.token = None
        self.token_expiry = None
        self.token_buffer = 300  # 5 min buffer
        
        self._tm_cache = {}
        self._tb_cache = {}
    
    def login(self) -> bool:
        """Authenticate with memoQ Server"""
        url = f"{self.server_url}{self.base_path}/auth/login"
        payload = {
            "UserName": self.username,
            "Password": self.password,
            "LoginMode": 0
        }
        
        try:
            response = requests.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
                verify=self.verify_ssl,
                timeout=self.timeout
            )
            response.raise_for_status()
            
            data = response.json()
            self.token = data.get("AccessToken")
            self.token_expiry = datetime.now() + timedelta(minutes=55)
            
            logger.info(f"✓ Authenticated as {data.get('Name')}")
            return True
            
        except Exception as e:
            logger.error(f"Login failed: {e}")
            raise Exception(f"Authentication failed: {str(e)}")
    
    def _ensure_token(self) -> bool:
        """Ensure token is valid"""
        if self.token is None:
            return self.login()
        
        if datetime.now() > (self.token_expiry - timedelta(seconds=self.token_buffer)):
            logger.warning("Token expiring, refreshing...")
            return self.login()
        
        return True
    
    def _make_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None
    ) -> Dict:
        """Make REST API request"""
        if not self._ensure_token():
            raise Exception("Authentication failed")
        
        url = f"{self.server_url}{self.base_path}{endpoint}"

        # Use Authorization header (best practice per memoQ API docs)
        # instead of ?authToken= query parameter
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"MQS-API {self.token}"
        }

        try:
            if method == "GET":
                response = requests.get(
                    url,
                    params=params,
                    headers=headers,
                    verify=self.verify_ssl,
                    timeout=self.timeout
                )
            elif method == "POST":
                logger.debug(f"POST {url}")
                logger.debug(f"POST data: {data}")
                response = requests.post(
                    url,
                    json=data,
                    params=params,
                    headers=headers,
                    verify=self.verify_ssl,
                    timeout=self.timeout
                )
            else:
                raise ValueError(f"Unsupported method: {method}")

            logger.debug(f"Response {response.status_code}: {response.text[:500]}")

            response.raise_for_status()
            return response.json()

        except requests.exceptions.HTTPError as e:
            error_code = "Unknown"
            error_msg = str(e)
            try:
                error_data = response.json()
                if isinstance(error_data, dict):
                    error_code = error_data.get("ErrorCode", "Unknown")
                    error_msg = error_data.get("Message", str(e))
            except Exception:
                pass  # Response body not JSON or not a dict

            # Auto-recover on expired token
            if error_code == "InvalidOrExpiredToken":
                logger.warning("Token expired, attempting re-login...")
                self.token = None
                self.token_expiry = None
                if self.login():
                    # Retry once with refreshed Authorization header
                    headers["Authorization"] = f"MQS-API {self.token}"
                    if method == "GET":
                        retry_resp = requests.get(url, params=params, headers=headers,
                                                  verify=self.verify_ssl, timeout=self.timeout)
                    else:
                        retry_resp = requests.post(url, json=data, params=params, headers=headers,
                                                   verify=self.verify_ssl, timeout=self.timeout)
                    retry_resp.raise_for_status()
                    return retry_resp.json()
                else:
                    raise Exception("Re-authentication failed after token expiry")

            raise Exception(f"HTTP {response.status_code}: {error_code}: {error_msg}")

        except Exception as e:
            logger.error(f"Request failed: {str(e)}")
            raise Exception(f"Request failed: {str(e)}")
    
    # ==================== TRANSLATION MEMORY ====================
    
    def list_tms(
        self,
        src_lang: Optional[str] = None,
        tgt_lang: Optional[str] = None,
        force_refresh: bool = False
    ) -> List[Dict]:
        """List all Translation Memories"""
        cache_key = f"tms_{src_lang}_{tgt_lang}"
        
        if not force_refresh and cache_key in self._tm_cache:
            return self._tm_cache[cache_key]
        
        endpoint = "/tms"
        params = {}
        
        if src_lang:
            params["srcLang"] = src_lang
        if tgt_lang:
            params["targetLang"] = tgt_lang
        
        result = self._make_request("GET", endpoint, params=params if params else None)
        self._tm_cache[cache_key] = result
        
        logger.info(f"Listed {len(result)} TMs")
        return result
    
    def lookup_segments(
        self,
        tm_guid: str,
        segments: List[str],
        match_threshold: int = 70,
        src_lang: Optional[str] = None,
        tgt_lang: Optional[str] = None,
        context_info: Optional[List[Dict]] = None
    ) -> Dict:
        """
        Lookup segments in Translation Memory

        Args:
            tm_guid: Translation Memory GUID
            segments: List of source segments to lookup
            match_threshold: Minimum match percentage (50-102)
            src_lang: Source language code (e.g., 'eng')
            tgt_lang: Target language code (e.g., 'tur')
            context_info: Optional list of dicts with context for each segment:
                          [{"preceding": "prev text", "following": "next text"}, ...]
                          Enables 101% context matching in memoQ TM.

        Returns:
            Dict with normalized TMMatch objects: {segment_index: [TMMatch objects]}
        """
        # Build payload according to memoQ API v1 documentation
        # IMPORTANT: Segments must be wrapped in <seg> XML tags
        # Context fields (PrecedingSegment, FollowingSegment) enable 101% context matches
        segment_entries = []
        for i, seg in enumerate(segments):
            entry = {"Segment": f"<seg>{seg}</seg>"}
            # Add context info if available for this segment
            if context_info and i < len(context_info) and context_info[i]:
                ctx = context_info[i]
                if ctx.get("preceding"):
                    entry["PrecedingSegment"] = f"<seg>{ctx['preceding']}</seg>"
                if ctx.get("following"):
                    entry["FollowingSegment"] = f"<seg>{ctx['following']}</seg>"
            segment_entries.append(entry)

        payload = {
            "Segments": segment_entries,
            "Options": {
                "MatchThreshold": match_threshold,
                "AdjustFuzzyMatches": False,
                "InlineTagStrictness": 2,
                "OnlyBest": False,
                "OnlyUnambiguous": False,
                "ShowFragmentHits": False,
                "ReverseLookup": False
            }
        }

        # Add language filtering if provided
        if src_lang:
            payload["SourceLanguage"] = src_lang
        if tgt_lang:
            payload["TargetLanguage"] = tgt_lang
        
        endpoint = f"/tms/{tm_guid}/lookupsegments"

        try:
            logger.info(f"🔍 TM LOOKUP REQUEST:")
            logger.info(f"  TM GUID: {tm_guid}")
            logger.info(f"  Source Lang: {src_lang}")
            logger.info(f"  Target Lang: {tgt_lang}")
            logger.info(f"  Segments count: {len(segments)}")
            logger.info(f"  Match threshold: {match_threshold}")
            logger.debug(f"  Full payload: {payload}")

            result = self._make_request("POST", endpoint, data=payload)

            logger.info(f"📥 TM LOOKUP RESPONSE:")
            logger.info(f"  Raw response type: {type(result)}")
            logger.info(f"  Full response: {result}")
            
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
        except Exception as e:
            logger.error(f"TM lookup error: {e}", exc_info=True)
            return {}
    
    def concordance_search(
        self,
        tm_guid: str,
        search_terms: List[str],
        results_limit: int = 64
    ) -> Dict:
        """Concordance search in Translation Memory"""
        payload = {
            "SearchExpression": search_terms,
            "Options": {
                "ResultsLimit": results_limit,
                "Ascending": False,
                "Column": 3
            }
        }
        
        endpoint = f"/tms/{tm_guid}/concordance"
        return self._make_request("POST", endpoint, data=payload)
    
    # ==================== TERMBASE ====================
    
    def list_tbs(
        self,
        languages: Optional[List[str]] = None,
        force_refresh: bool = False
    ) -> List[Dict]:
        """List all Termbases"""
        cache_key = f"tbs_{'_'.join(languages or [])}"
        
        if not force_refresh and cache_key in self._tb_cache:
            return self._tb_cache[cache_key]
        
        endpoint = "/tbs"
        params = None
        
        if languages:
            params = {f"lang[{i}]": lang for i, lang in enumerate(languages)}
        
        result = self._make_request("GET", endpoint, params=params)
        self._tb_cache[cache_key] = result
        
        logger.info(f"Listed {len(result)} TBs")
        return result
    
    def lookup_terms(
        self,
        tb_guid: str,
        search_terms: List[str],
        src_lang: str = "eng",
        tgt_lang: Optional[str] = "tur"
    ) -> List:
        """
        Lookup terms in Termbase
        
        Args:
            tb_guid: Termbase GUID
            search_terms: List of terms to lookup
            src_lang: Source language code (default: "eng" for English)
            tgt_lang: Target language code (optional, default: "tur" for Turkish)
        
        Returns:
            List of normalized TermMatch objects
        """
        # Clean search terms: remove XML inline tags AND {{N}} placeholders
        cleaned_terms = []
        for term in search_terms:
            # Remove XML inline tags: <bpt>, <ept>, <ph>, <it>, etc.
            clean_text = re.sub(r'<[^>]+>', '', term)
            # Remove {{N}} style tag placeholders
            clean_text = re.sub(r'\{\{[^}]+\}\}', '', clean_text)
            # Clean up extra whitespace
            clean_text = re.sub(r'\s+', ' ', clean_text).strip()
            cleaned_terms.append(clean_text)
        
        # Build correct payload according to memoQ API v1 documentation
        # IMPORTANT: Segments must be wrapped in <seg> XML tags
        payload = {
            "SourceLanguage": src_lang,
            "Segments": [f"<seg>{term}</seg>" for term in cleaned_terms]
        }
        
        # Add target language if specified
        if tgt_lang:
            payload["TargetLanguage"] = tgt_lang
        
        endpoint = f"/tbs/{tb_guid}/lookupterms"
        
        try:
            result = self._make_request("POST", endpoint, data=payload)

            # Use 'is not None' instead of truthiness check — empty list [] is valid but falsy
            if result is not None:
                normalized_terms = normalize_memoq_tb_response(
                    result,
                    src_lang=src_lang,
                    tgt_lang=tgt_lang
                )
                return normalized_terms
            else:
                return []
        except Exception as e:
            logger.error(f"TB lookup FAILED: {e}", exc_info=True)
            return []
