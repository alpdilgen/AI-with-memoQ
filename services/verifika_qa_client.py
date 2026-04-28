"""
Verifika QA API Client
Handles authentication, project lifecycle, file upload, QA execution, and
quality issue retrieval against the Verifika cloud QA API.

API base:  https://beta.e-verifika.com
Docs:      https://documenter.getpostman.com/view/50753647/2sBXqGsNJk

Authentication strategy
-----------------------
The client supports two flows:

  1. Bearer token (preferred for headless use): pass `api_token=...`
     to the constructor. Header: `Authorization: Bearer <token>`.
  2. Username/password login: call `login()` after construction; the
     client requests a token from /api/auth/login and refreshes it
     before expiry. Used when no long-lived API token is available.

Both flows put the same `Authorization: Bearer <token>` header on every
request afterwards.

Designed to be reusable: the same client class is used by the Streamlit
UI today and is easy to lift into a standalone backend service later.
"""

from __future__ import annotations

import io
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Defaults
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_BASE_URL = "https://beta.e-verifika.com"
DEFAULT_API_VERSION = "1.0"
DEFAULT_CHUNK_SIZE = 5 * 1024 * 1024   # 5 MB per chunk for UploadChunkFile
DEFAULT_POLL_INTERVAL = 3              # seconds between polling tries
DEFAULT_POLL_TIMEOUT = 300             # 5 minutes max per QA report

# issueType code → human-readable label.
# Verifika docs do not publish the full enum; values below are derived from
# observed responses and may need refinement once we run real reports.
ISSUE_TYPE_LABELS = {
    0: "Spelling",
    1: "Terminology",
    2: "Punctuation",
    3: "Formatting",
    4: "Untranslatables",
    5: "Grammar",
}


# ─────────────────────────────────────────────────────────────────────────────
# Data classes (lightweight; we keep dicts on the wire and only normalise)
# ─────────────────────────────────────────────────────────────────────────────

class VerifikaError(Exception):
    """Raised for any Verifika API error (HTTP, auth, business)."""
    def __init__(self, message: str, status_code: Optional[int] = None,
                 response_body: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


# ─────────────────────────────────────────────────────────────────────────────
# Client
# ─────────────────────────────────────────────────────────────────────────────

class VerifikaQAClient:
    """REST client for Verifika QA API."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        api_token: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        api_version: str = DEFAULT_API_VERSION,
        verify_ssl: bool = True,
        timeout: int = 60,
    ):
        """
        Args:
            base_url:    API root (no trailing slash). Default beta.e-verifika.com.
            api_token:   Long-lived Bearer token. If provided, login() is skipped.
            username:    For password-based login. Only used if api_token is None.
            password:    For password-based login.
            api_version: Sent as ?api-version= on every request.
            verify_ssl:  TLS verify flag (True for production).
            timeout:     Per-request timeout (seconds).
        """
        self.base_url = base_url.rstrip("/")
        self.api_version = api_version
        self.verify_ssl = verify_ssl
        self.timeout = timeout

        self._token: Optional[str] = api_token
        self._token_expiry: Optional[datetime] = None
        self._username = username
        self._password = password

        self._session = requests.Session()

    # ── Auth ────────────────────────────────────────────────────────────────

    def login(self) -> None:
        """
        Authenticate with username/password and obtain a Bearer token.
        Skipped if `api_token` was passed to the constructor.
        """
        if self._token and not self._username:
            # Static token mode — nothing to do.
            return

        if not (self._username and self._password):
            raise VerifikaError(
                "Cannot login: no api_token and no username/password provided"
            )

        # Endpoint name follows the pattern documented in the Postman
        # collection. If the real endpoint differs (e.g. /api/auth/token)
        # we adjust here without touching call sites.
        url = f"{self.base_url}/api/auth/login"
        payload = {"username": self._username, "password": self._password}

        resp = self._session.post(
            url, json=payload,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            params={"api-version": self.api_version},
            timeout=self.timeout, verify=self.verify_ssl,
        )
        if resp.status_code >= 400:
            raise VerifikaError(
                f"Login failed: HTTP {resp.status_code}",
                status_code=resp.status_code,
                response_body=resp.text,
            )
        data = resp.json()
        self._token = data.get("token") or data.get("accessToken") or data.get("access_token")
        if not self._token:
            raise VerifikaError(f"Login response missing token: {data}")

        # If response contains expiry, honour it; otherwise assume 1 h.
        expires_in = data.get("expiresIn") or data.get("expires_in") or 3600
        self._token_expiry = datetime.utcnow() + timedelta(seconds=int(expires_in) - 60)
        logger.info("Verifika: authenticated, token expires in %ss", expires_in)

    def _ensure_auth(self) -> None:
        """Refresh token if it is missing or close to expiry."""
        if not self._token:
            self.login()
            return
        if self._token_expiry and datetime.utcnow() >= self._token_expiry:
            logger.info("Verifika: token expired, refreshing")
            self._token = None
            self.login()

    # ── HTTP plumbing ───────────────────────────────────────────────────────

    def _headers(self, extra: Optional[Dict] = None) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._token}" if self._token else "",
        }
        if extra:
            headers.update(extra)
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict] = None,
        json_body: Optional[Dict] = None,
        data: Optional[bytes] = None,
        headers: Optional[Dict] = None,
        accept_text: bool = False,
    ) -> Dict | str | bytes:
        """
        Single point for every HTTP request. Adds api-version, Bearer
        header, retries once on 401, and decodes JSON or raw bytes.
        """
        self._ensure_auth()
        url = f"{self.base_url}{path}"
        merged_params = {"api-version": self.api_version, **(params or {})}
        merged_headers = self._headers(headers)
        if accept_text:
            merged_headers["Accept"] = "text/plain"

        for attempt in (1, 2):
            resp = self._session.request(
                method, url,
                params=merged_params,
                json=json_body if data is None else None,
                data=data,
                headers=merged_headers,
                timeout=self.timeout,
                verify=self.verify_ssl,
            )

            # Auto-recover on expired token (only if username/password is set)
            if resp.status_code == 401 and attempt == 1 and self._username:
                logger.warning("Verifika: 401, re-authenticating and retrying once")
                self._token = None
                self._ensure_auth()
                merged_headers = self._headers(headers)
                if accept_text:
                    merged_headers["Accept"] = "text/plain"
                continue

            if resp.status_code >= 400:
                raise VerifikaError(
                    f"{method} {path} failed: HTTP {resp.status_code}",
                    status_code=resp.status_code,
                    response_body=resp.text[:1000],
                )

            ctype = resp.headers.get("Content-Type", "")
            if "application/json" in ctype:
                return resp.json()
            return resp.text if accept_text else resp.content

        # Should be unreachable
        raise VerifikaError(f"{method} {path}: exhausted retries")

    def _request_multipart(
        self,
        method: str,
        path: str,
        *,
        files: Dict,
        data_fields: Optional[Dict] = None,
        params: Optional[Dict] = None,
    ) -> Dict | str | bytes:
        """
        Multipart/form-data variant of _request.

        Used for endpoints that expect a real multipart upload such as
        /api/ProjectFiles/UploadChunkFile, where the server validates
        named form fields (File, Index, FileId, FileName, ProjectId).

        Args:
            files:        dict for `requests.post(files=...)`, e.g.
                          {"File": (filename, bytes, "application/xml")}
            data_fields:  dict of form fields sent alongside the file
                          (FileName, ProjectId, Index, FileId, ...).
            params:       optional query string (api-version is added).

        Returns parsed JSON or raw text/bytes per server response.
        """
        self._ensure_auth()
        url = f"{self.base_url}{path}"
        merged_params = {"api-version": self.api_version, **(params or {})}
        # NOTE: do NOT set Content-Type — requests builds the multipart
        # boundary header automatically.
        merged_headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._token}" if self._token else "",
        }

        for attempt in (1, 2):
            resp = self._session.request(
                method, url,
                params=merged_params,
                files=files,
                data=data_fields or {},
                headers=merged_headers,
                timeout=self.timeout,
                verify=self.verify_ssl,
            )

            if resp.status_code == 401 and attempt == 1 and self._username:
                logger.warning("Verifika: 401 on multipart, re-auth + retry once")
                self._token = None
                self._ensure_auth()
                merged_headers["Authorization"] = f"Bearer {self._token}"
                continue

            if resp.status_code >= 400:
                raise VerifikaError(
                    f"{method} {path} failed: HTTP {resp.status_code}",
                    status_code=resp.status_code,
                    response_body=resp.text[:1500],
                )

            ctype = resp.headers.get("Content-Type", "")
            if "application/json" in ctype:
                return resp.json()
            return resp.text or resp.content

        raise VerifikaError(f"{method} {path}: exhausted retries (multipart)")

    # ── User / health ───────────────────────────────────────────────────────

    def get_current_user(self) -> Dict:
        """`GET /api/Users/current` — sanity check the auth header works."""
        return self._request("GET", "/api/Users/current")

    # ── QA settings (profiles) ─────────────────────────────────────────────

    def list_qa_settings(self) -> List[Dict]:
        """`GET /api/QASettings` — list available QA profiles."""
        result = self._request("GET", "/api/QASettings")
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            # Some Verifika endpoints wrap lists in {"value": [...]} or {"items": [...]}
            for key in ("value", "items", "results"):
                if key in result and isinstance(result[key], list):
                    return result[key]
        return []

    # ── Project lifecycle ───────────────────────────────────────────────────

    def create_project(self, name: str, source_lang: Optional[str] = None,
                       target_lang: Optional[str] = None) -> Dict:
        """
        `POST /api/Projects` — create an empty Verifika project.

        Args:
            name: Display name (we use XLIFF base name + timestamp).
            source_lang / target_lang: optional language hints (memoQ codes
                e.g. 'eng-US', 'tur'). Verifika may accept these as
                metadata; we send them when known.

        Returns the project dict including its `id` (uuid).
        """
        body: Dict = {"name": name}
        if source_lang:
            body["sourceLanguage"] = source_lang
        if target_lang:
            body["targetLanguage"] = target_lang
        return self._request("POST", "/api/Projects", json_body=body)

    def assign_qa_profile(self, project_id: str, qa_settings_id: str) -> Dict:
        """`POST /api/Projects/:id/ChangeQASettings` — apply a QA profile."""
        return self._request(
            "POST", f"/api/Projects/{project_id}/ChangeQASettings",
            json_body={"qaSettingsId": qa_settings_id},
        )

    # ── File upload (chunked) ───────────────────────────────────────────────

    def upload_file(
        self,
        project_id: str,
        file_bytes: bytes,
        file_name: str,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        progress_cb: Optional[Callable[[int, int], None]] = None,
    ) -> Dict:
        """
        Chunked upload to /api/ProjectFiles/UploadChunkFile, then
        /api/ProjectFiles/CommitFile to finalize.

        Real-server contract (PascalCase, multipart/form-data):
            File:      multipart file blob (one chunk)
            Index:     0-based chunk index
            FileId:    stable uuid-like id shared by every chunk of one file
            FileName:  original file name
            ProjectId: target project uuid

        Args:
            project_id:  Verifika project uuid.
            file_bytes:  XLIFF bytes (final translated file).
            file_name:   Original filename for display in Verifika.
            chunk_size:  Bytes per chunk (default 5 MB).
            progress_cb: Optional callable(uploaded_bytes, total_bytes).

        Returns the CommitFile response (file metadata dict).
        """
        import uuid

        total = len(file_bytes)
        if total == 0:
            raise VerifikaError("Cannot upload empty file")

        # Stable identifier across chunks of this file. Uuid keeps it
        # collision-free across concurrent uploads.
        file_id = str(uuid.uuid4())

        uploaded = 0
        chunk_idx = 0
        uploaded_indices: list = []

        with io.BytesIO(file_bytes) as buf:
            while uploaded < total:
                chunk = buf.read(chunk_size)
                if not chunk:
                    break
                self._request_multipart(
                    "POST", "/api/ProjectFiles/UploadChunkFile",
                    files={
                        "File": (file_name, chunk, "application/octet-stream"),
                    },
                    data_fields={
                        "Index":     str(chunk_idx),
                        "FileId":    file_id,
                        "FileName":  file_name,
                        "ProjectId": project_id,
                    },
                )
                uploaded_indices.append(chunk_idx)
                uploaded += len(chunk)
                chunk_idx += 1
                if progress_cb:
                    progress_cb(uploaded, total)

        # Commit — server expects PascalCase here too.
        # 'Indices' is the list of chunk indices that were successfully
        # uploaded (server validates 0..N coverage and order).
        commit_resp = self._request(
            "POST", "/api/ProjectFiles/CommitFile",
            json_body={
                "ProjectId":   project_id,
                "FileName":    file_name,
                "FileId":      file_id,
                "Indices":     ",".join(str(i) for i in uploaded_indices),
                "TotalSize":   total,
                "TotalChunks": chunk_idx,
            },
        )
        logger.info("Verifika: committed file %s (%d bytes, %d chunks, fileId=%s)",
                    file_name, total, chunk_idx, file_id)
        return commit_resp

    # ── Reports ─────────────────────────────────────────────────────────────

    def create_report(self, project_id: str) -> str:
        """
        `POST /api/Reports` — generate a new report id for this project.
        Returns the report uuid (string).
        """
        result = self._request("POST", "/api/Reports", json_body={"projectId": project_id})
        # Response shape is documented as containing `id` or `reportId`.
        if isinstance(result, dict):
            return result.get("id") or result.get("reportId") or result.get("ReportId", "")
        return str(result)

    def run_report(self, report_id: str) -> Dict:
        """`POST /api/Reports/:id/GenerateLink` — start the analysis."""
        return self._request("POST", f"/api/Reports/{report_id}/GenerateLink")

    def get_report_status(self, report_id: str) -> Dict:
        """`GET /api/Reports/:id` — fetch status of every issueType."""
        return self._request("GET", f"/api/Reports/{report_id}")

    def wait_for_report(
        self,
        report_id: str,
        poll_interval: int = DEFAULT_POLL_INTERVAL,
        timeout: int = DEFAULT_POLL_TIMEOUT,
        progress_cb: Optional[Callable[[Dict], None]] = None,
    ) -> Dict:
        """
        Poll until every `issueType` has `status == 1` (ready) or timeout
        is reached. Returns the final status dict.

        progress_cb (if given) is called with the latest status payload
        each tick — UI can show "3/6 categories ready" etc.
        """
        deadline = time.time() + timeout
        last_status: Dict = {}
        while time.time() < deadline:
            status = self.get_report_status(report_id)
            last_status = status
            if progress_cb:
                progress_cb(status)
            statuses = self._extract_statuses(status)
            if statuses and all(s.get("status") == 1 for s in statuses):
                return status
            time.sleep(poll_interval)

        raise VerifikaError(
            f"Report {report_id} not ready after {timeout}s "
            f"(last status: {last_status})"
        )

    @staticmethod
    def _extract_statuses(status_payload: Dict) -> List[Dict]:
        """Pull the `statuses` array out of a /api/Reports/:id response."""
        if not isinstance(status_payload, dict):
            return []
        if "statuses" in status_payload and isinstance(status_payload["statuses"], list):
            return status_payload["statuses"]
        # Some servers nest under "data" or "report"
        for k in ("data", "report"):
            inner = status_payload.get(k)
            if isinstance(inner, dict) and isinstance(inner.get("statuses"), list):
                return inner["statuses"]
        return []

    # ── Quality issues ─────────────────────────────────────────────────────

    def get_quality_issues(self, project_id: str,
                           report_id: Optional[str] = None) -> List[Dict]:
        """
        `GET /api/QualityIssues` — fetch issues for a project (and
        optionally a specific report).

        Returns a flat list of issue dicts with at minimum:
            { 'segmentId', 'issueType', 'severity', 'message',
              'sourceText', 'targetText', 'suggestion' }
        Field names are normalised across response shapes.
        """
        params: Dict = {"projectId": project_id}
        if report_id:
            params["reportId"] = report_id

        result = self._request("GET", "/api/QualityIssues", params=params)
        raw_list: List[Dict] = []
        if isinstance(result, list):
            raw_list = result
        elif isinstance(result, dict):
            for key in ("value", "items", "results", "issues"):
                if isinstance(result.get(key), list):
                    raw_list = result[key]
                    break

        return [self._normalise_issue(it) for it in raw_list if isinstance(it, dict)]

    @staticmethod
    def _normalise_issue(it: Dict) -> Dict:
        """
        Normalise a single issue dict to a stable schema regardless of
        the field-name flavour the server returns (camelCase vs PascalCase).
        """
        def pick(*keys, default=""):
            for k in keys:
                if k in it and it[k] not in (None, ""):
                    return it[k]
            return default

        issue_type = pick("issueType", "IssueType", "type", default=-1)
        try:
            issue_type_int = int(issue_type)
        except (TypeError, ValueError):
            issue_type_int = -1

        return {
            "id":           pick("id", "Id", default=""),
            "segmentId":    pick("segmentId", "SegmentId", "segId", default=""),
            "issueType":    issue_type_int,
            "issueLabel":   ISSUE_TYPE_LABELS.get(issue_type_int, f"Type {issue_type_int}"),
            "severity":     pick("severity", "Severity", default="warning"),
            "message":      pick("message", "Message", "description", "Description", default=""),
            "sourceText":   pick("sourceText", "SourceText", "source", "Source", default=""),
            "targetText":   pick("targetText", "TargetText", "target", "Target", default=""),
            "suggestion":   pick("suggestion", "Suggestion", default=""),
            "raw":          it,
        }

    # ── High-level convenience: end-to-end QA ──────────────────────────────

    def run_full_qa(
        self,
        project_name: str,
        xliff_bytes: bytes,
        xliff_filename: str,
        qa_settings_id: str,
        source_lang: Optional[str] = None,
        target_lang: Optional[str] = None,
        progress_cb: Optional[Callable[[str, Dict], None]] = None,
    ) -> Tuple[str, List[Dict]]:
        """
        One-shot: create project → upload file → assign profile →
        generate report → wait → return issues.

        Args:
            project_name:    Display name (e.g. XLIFF basename + timestamp).
            xliff_bytes:     Translated XLIFF content.
            xliff_filename:  File name to register on Verifika side.
            qa_settings_id:  Profile uuid (from list_qa_settings).
            source_lang:     Optional source language hint.
            target_lang:     Optional target language hint.
            progress_cb:     Optional callable(stage, payload) for UI progress.
                             stages: 'project_created', 'file_uploaded',
                                     'profile_assigned', 'report_started',
                                     'report_progress', 'issues_fetched'.

        Returns:
            (project_id, [normalised issue dicts])
        """
        def _emit(stage, payload):
            if progress_cb:
                try: progress_cb(stage, payload)
                except Exception: pass

        # 1. Project
        project = self.create_project(project_name, source_lang, target_lang)
        project_id = project.get("id") or project.get("Id") or ""
        if not project_id:
            raise VerifikaError(f"Project create response missing id: {project}")
        _emit("project_created", project)

        # 2. Upload
        commit = self.upload_file(project_id, xliff_bytes, xliff_filename)
        _emit("file_uploaded", commit)

        # 3. QA profile
        self.assign_qa_profile(project_id, qa_settings_id)
        _emit("profile_assigned", {"qaSettingsId": qa_settings_id})

        # 4. Report
        report_id = self.create_report(project_id)
        if not report_id:
            raise VerifikaError("Report create returned empty id")
        self.run_report(report_id)
        _emit("report_started", {"reportId": report_id})

        # 5. Poll
        final_status = self.wait_for_report(
            report_id,
            progress_cb=lambda s: _emit("report_progress", s),
        )

        # 6. Issues
        issues = self.get_quality_issues(project_id, report_id)
        _emit("issues_fetched", {"count": len(issues), "status": final_status})
        return project_id, issues
