"""
Verifika QA API Client (v2 — task-based workflow)

Built from a real HAR capture of the Verifika web UI's "Start QA" button.
The actual server contract is task-based, NOT report-based as the public
Postman docs imply. The chain is:

    1. POST  /api/Projects                          (create project,
                                                    optionally with qaSettingsId)
    2. POST  /api/ProjectFiles/UploadChunkFile      (multipart, repeated)
    3. POST  /api/ProjectFiles/CommitFile           (best-effort; file is
                                                    typically usable even
                                                    without it)
    4. POST  /api/Projects/{pid}/start              <— this single call
                                                    creates the task and
                                                    assigns it
        body: {"assignments":[{"allFiles":true,
                               "assignedToId":"<user-guid>"}]}
    5. GET   /api/projects/{pid}/tasks              (read taskId)
    6. POST  /api/projects/{pid}/tasks/accept       (no body)
    7. POST  /api/projects/{pid}/tasks/{tid}/check  (no body — runs QA)
    8. GET   /api/projects/{pid}/tasks              (poll: leftCount → 0)
    9. GET   /api/QualityIssues?projectId=...&taskId=...

The web UI also opens https://beta.e-verifika.com/report/{pid}/formal
to display the rich QA review screen. We expose that URL too so the
Streamlit tab can offer an iframe + open-in-new-tab fallback.

Authentication
--------------
Long-lived Bearer token (preferred). The token is the value of
`access_token` in the Verifika web app's localStorage. JWT payload
typically contains `sub` (user GUID) — useful for assignedToId without
calling /api/Users/current.

This client is UI-agnostic (no streamlit imports), so it can be lifted
into a standalone backend later.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import time
import uuid
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Defaults
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_BASE_URL = "https://beta.e-verifika.com"
DEFAULT_API_VERSION = "1.0"
DEFAULT_CHUNK_SIZE = 5 * 1024 * 1024   # 5 MB per chunk
DEFAULT_POLL_INTERVAL = 3              # seconds
DEFAULT_POLL_TIMEOUT = 600             # 10 minutes max

# issueType code → human label.
# Verifika does not publish the enum; values learned by inspection.
ISSUE_TYPE_LABELS = {
    0: "Spelling",
    1: "Terminology",
    2: "Punctuation",
    3: "Formatting",
    4: "Untranslatables",
    5: "Grammar",
}


class VerifikaError(Exception):
    """Raised for any Verifika API failure (HTTP, auth, validation)."""
    def __init__(self, message: str,
                 status_code: Optional[int] = None,
                 response_body: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


# ─────────────────────────────────────────────────────────────────────────────
# Client
# ─────────────────────────────────────────────────────────────────────────────

class VerifikaQAClient:
    """REST client for the Verifika QA API."""

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
        self.base_url = base_url.rstrip("/")
        self.api_version = api_version
        self.verify_ssl = verify_ssl
        self.timeout = timeout

        self._token: Optional[str] = api_token
        self._token_expiry: Optional[datetime] = None
        self._username = username
        self._password = password
        self._cached_user_id: Optional[str] = None

        self._session = requests.Session()

    # ── Auth ────────────────────────────────────────────────────────────────

    def login(self) -> None:
        """Login with username/password (skipped when api_token is set)."""
        if self._token and not self._username:
            return
        if not (self._username and self._password):
            raise VerifikaError(
                "Cannot login: no api_token and no username/password"
            )
        url = f"{self.base_url}/api/auth/login"
        resp = self._session.post(
            url,
            json={"username": self._username, "password": self._password},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            params={"api-version": self.api_version},
            timeout=self.timeout, verify=self.verify_ssl,
        )
        if resp.status_code >= 400:
            raise VerifikaError(
                f"Login failed: HTTP {resp.status_code}",
                status_code=resp.status_code, response_body=resp.text)
        data = resp.json()
        self._token = (data.get("token") or data.get("accessToken")
                       or data.get("access_token"))
        if not self._token:
            raise VerifikaError(f"Login response missing token: {data}")
        expires_in = int(data.get("expiresIn") or data.get("expires_in") or 3600)
        self._token_expiry = datetime.utcnow() + timedelta(seconds=expires_in - 60)

    def _ensure_auth(self) -> None:
        if not self._token:
            self.login(); return
        if self._token_expiry and datetime.utcnow() >= self._token_expiry:
            self._token = None
            self.login()

    # ── User / GUID extraction ─────────────────────────────────────────────

    def get_current_user_id(self) -> str:
        """
        Return the GUID of the currently authenticated user.

        Strategy:
          1. Cache hit
          2. Decode JWT payload's `sub` claim (no HTTP call)
          3. Fallback: GET /api/Users/current
        """
        if self._cached_user_id:
            return self._cached_user_id

        # JWT decode (no signature verification — we trust the issuer)
        if self._token and self._token.count(".") == 2:
            try:
                _, payload_b64, _ = self._token.split(".")
                # base64url padding
                pad = "=" * (-len(payload_b64) % 4)
                payload = json.loads(base64.urlsafe_b64decode(payload_b64 + pad))
                uid = (payload.get("sub") or payload.get("userId")
                       or payload.get("id"))
                if uid:
                    self._cached_user_id = uid
                    return uid
            except Exception as e:
                logger.debug("JWT decode failed: %s", e)

        # Fallback to /api/Users/current
        try:
            data = self._request("GET", "/api/Users/current")
            uid = data.get("id") or data.get("Id")
            if uid:
                self._cached_user_id = uid
                return uid
        except VerifikaError:
            pass

        raise VerifikaError(
            "Could not determine current user GUID — "
            "JWT 'sub' claim missing and /api/Users/current failed"
        )

    def get_current_user(self) -> Dict:
        """`GET /api/Users/current` — full user record."""
        return self._request("GET", "/api/Users/current")

    # ── HTTP plumbing ───────────────────────────────────────────────────────

    def _headers(self, extra: Optional[Dict] = None) -> Dict[str, str]:
        h = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._token}" if self._token else "",
        }
        if extra:
            h.update(extra)
        return h

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict] = None,
        json_body=None,
        data: Optional[bytes] = None,
        headers: Optional[Dict] = None,
        accept_text: bool = False,
        api_version_override: Optional[str] = None,
    ):
        """JSON-ish request. json_body may be dict OR list."""
        self._ensure_auth()
        url = f"{self.base_url}{path}"
        merged_params = {
            "api-version": api_version_override or self.api_version,
            **(params or {}),
        }
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

            if resp.status_code == 401 and attempt == 1 and self._username:
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
            # Verifika often returns "text/json" too
            if "json" in ctype:
                if not resp.text:
                    return None
                return resp.json()
            return resp.text if accept_text else resp.content

        raise VerifikaError(f"{method} {path}: exhausted retries")

    def _request_multipart(
        self,
        method: str,
        path: str,
        *,
        files: Dict,
        data_fields: Optional[Dict] = None,
        params: Optional[Dict] = None,
    ):
        """Multipart/form-data variant for chunk uploads."""
        self._ensure_auth()
        url = f"{self.base_url}{path}"
        merged_params = {"api-version": self.api_version, **(params or {})}
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
            if "json" in ctype:
                if not resp.text:
                    return None
                return resp.json()
            return resp.text or resp.content

        raise VerifikaError(f"{method} {path}: exhausted retries (multipart)")

    # ── QA settings ─────────────────────────────────────────────────────────

    def list_qa_settings(self) -> List[Dict]:
        """`GET /api/QASettings` — list available QA profiles."""
        result = self._request("GET", "/api/QASettings",
                               api_version_override="1.1")
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            for k in ("value", "items", "results", "data"):
                if isinstance(result.get(k), list):
                    return result[k]
        return []

    # ── Project lifecycle ───────────────────────────────────────────────────

    def create_project(
        self,
        name: str,
        qa_settings_id: Optional[str] = None,
        source_lang: Optional[str] = None,
        target_lang: Optional[str] = None,
    ) -> Dict:
        """
        `POST /api/Projects` — create a project.

        If `qa_settings_id` is provided, it is set on the project at
        creation time (matches what the Verifika UI does).
        """
        body: Dict = {"name": name}
        if qa_settings_id:
            body["qaSettingsId"] = qa_settings_id
        if source_lang:
            body["sourceLanguage"] = source_lang
        if target_lang:
            body["targetLanguage"] = target_lang
        return self._request("POST", "/api/Projects", json_body=body)

    def get_project(self, project_id: str) -> Dict:
        """`GET /api/Projects/{id}`."""
        return self._request("GET", f"/api/Projects/{project_id}")

    # ── File upload ─────────────────────────────────────────────────────────

    def upload_file(
        self,
        project_id: str,
        file_bytes: bytes,
        file_name: str,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        progress_cb: Optional[Callable[[int, int], None]] = None,
        commit: bool = True,
    ) -> Dict:
        """
        Chunked multipart upload + commit.

        Per chunk (multipart/form-data):
            File:      blob
            Index:     0-based chunk index
            FileId:    uuid4 shared by all chunks of this file
            FileName:  original filename
            ProjectId: project uuid

        Commit (JSON body, if commit=True):
            ProjectId, FileName, FileId, Indices ("0,1,2"),
            TotalSize, TotalChunks
        """
        total = len(file_bytes)
        if total == 0:
            raise VerifikaError("Cannot upload empty file")

        file_id = str(uuid.uuid4())
        uploaded = 0
        chunk_idx = 0
        uploaded_indices: List[int] = []

        with io.BytesIO(file_bytes) as buf:
            while uploaded < total:
                chunk = buf.read(chunk_size)
                if not chunk:
                    break
                self._request_multipart(
                    "POST", "/api/ProjectFiles/UploadChunkFile",
                    files={"File": (file_name, chunk, "application/octet-stream")},
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

        commit_resp: Dict = {}
        if commit:
            try:
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
                ) or {}
            except VerifikaError as e:
                # The HAR shows that the file is usable even when CommitFile
                # complains (it persists during chunk upload). Log and move on.
                logger.warning("CommitFile failed (non-fatal): %s", e)

        return {"fileId": file_id, "totalChunks": chunk_idx,
                "commit": commit_resp}

    def list_project_files(self, project_id: str) -> List[Dict]:
        """`GET /api/projects/{pid}/projectFiles`."""
        result = self._request("GET",
                               f"/api/projects/{project_id}/projectFiles")
        return result if isinstance(result, list) else []

    # ── Tasks (the real QA driver) ──────────────────────────────────────────

    def start_project(
        self,
        project_id: str,
        assigned_to_id: str,
        all_files: bool = True,
    ) -> Dict:
        """
        `POST /api/Projects/{pid}/start` — what the UI's "Start QA" does.

        Creates a task assigned to the given user covering all (or selected)
        files. Required before /tasks/accept and /tasks/{id}/check.
        """
        body = {
            "assignments": [
                {"allFiles": all_files, "assignedToId": assigned_to_id}
            ]
        }
        return self._request(
            "POST", f"/api/Projects/{project_id}/start", json_body=body
        )

    def list_tasks(self, project_id: str) -> List[Dict]:
        """`GET /api/projects/{pid}/tasks`."""
        result = self._request("GET", f"/api/projects/{project_id}/tasks")
        return result if isinstance(result, list) else []

    def search_tasks(self, project_id: str, ids: List[str]) -> List[Dict]:
        """`POST /api/projects/{pid}/tasks/search` body: {"ids":[...]}"""
        result = self._request(
            "POST", f"/api/projects/{project_id}/tasks/search",
            json_body={"ids": list(ids)},
        )
        return result if isinstance(result, list) else []

    def accept_tasks(self, project_id: str) -> None:
        """`POST /api/projects/{pid}/tasks/accept` (no body)."""
        self._request("POST", f"/api/projects/{project_id}/tasks/accept",
                      json_body={})

    def run_qa_check(self, project_id: str, task_id: str) -> None:
        """
        `POST /api/projects/{pid}/tasks/{tid}/check` (no body) — kicks off
        the actual QA analysis on the task.
        """
        self._request(
            "POST",
            f"/api/projects/{project_id}/tasks/{task_id}/check",
            json_body={},
        )

    def wait_for_task_completion(
        self,
        project_id: str,
        task_id: str,
        poll_interval: int = DEFAULT_POLL_INTERVAL,
        timeout: int = DEFAULT_POLL_TIMEOUT,
        progress_cb: Optional[Callable[[Dict], None]] = None,
        min_settle_seconds: int = 6,
    ) -> Dict:
        """
        Poll until the task is completed.

        Verifika's task object exposes `status`, `acceptanceStatus`,
        `leftCount` and `correctedCount`. None of these alone proves
        completion (in our HAR they all stayed at 0 for a fresh task
        before issues appeared). The robust signal is the project
        endpoint's `taskSummary` — `checked`/`completed` go up only
        after QA actually finishes.

        Algorithm:
          1. Read the task to keep UI updated (progress_cb).
          2. Read the project; if `taskSummary.checked` or
             `taskSummary.completed` >= 1 → done.
          3. Also fetch /api/QualityIssues — if it returns a
             non-empty list, QA produced output → done.
          4. Otherwise wait `min_settle_seconds` after `tasks/{id}/check`
             before declaring "no issues" success (avoids racing the
             server which sometimes reports leftCount=0 instantly).
        """
        deadline = time.time() + timeout
        started_at = time.time()
        last_task: Dict = {}
        last_summary: Dict = {}

        while time.time() < deadline:
            # 1. Task status (for UI progress)
            try:
                tasks = self.list_tasks(project_id)
            except VerifikaError:
                tasks = []
            ours = [t for t in tasks if t.get("id") == task_id]
            if ours:
                last_task = ours[0]
                if progress_cb:
                    progress_cb(last_task)

            # 2. Project taskSummary — most reliable completion signal
            try:
                project = self.get_project(project_id)
                summary = project.get("taskSummary", {}) or {}
                last_summary = summary
                checked   = int(summary.get("checked", 0) or 0)
                completed = int(summary.get("completed", 0) or 0)
                review    = int(summary.get("review", 0) or 0)
                if checked >= 1 or completed >= 1 or review >= 1:
                    logger.info("Verifika: task done via taskSummary %s", summary)
                    return last_task or {"id": task_id, "summary": summary}
            except VerifikaError as e:
                logger.debug("get_project failed during poll: %s", e)

            # 3. QualityIssues — non-empty list also implies "done"
            try:
                issues = self.get_quality_issues(project_id, task_id)
                if issues:
                    logger.info("Verifika: task done — %d issues visible",
                                len(issues))
                    return last_task or {"id": task_id, "issuesFound": len(issues)}
            except VerifikaError:
                pass

            # 4. After a settle period without any signal, treat as
            #    "no issues found" success.
            elapsed = time.time() - started_at
            if elapsed >= min_settle_seconds and last_task:
                t = last_task
                if (int(t.get("leftCount", 0) or 0) == 0
                        and int(t.get("correctedCount", 0) or 0) == 0):
                    # Heuristic: server has been at zero for >= settle
                    # window AND project summary still empty → file has
                    # no detectable QA issues.
                    if elapsed >= min_settle_seconds * 3:
                        logger.info(
                            "Verifika: no signal after %.0fs — "
                            "assuming clean file (no issues)", elapsed)
                        return t

            time.sleep(poll_interval)

        raise VerifikaError(
            f"Task {task_id} not finished after {timeout}s "
            f"(last task: {last_task}, summary: {last_summary})"
        )

    # ── Quality issues ──────────────────────────────────────────────────────

    def get_quality_issues(
        self,
        project_id: str,
        task_id: Optional[str] = None,
    ) -> List[Dict]:
        """
        `GET /api/QualityIssues?projectId=...&taskId=...`
        Returns a flat list of normalised issue dicts.
        """
        params: Dict = {"projectId": project_id}
        if task_id:
            params["taskId"] = task_id
        result = self._request("GET", "/api/QualityIssues", params=params)

        raw_list: List[Dict] = []
        if isinstance(result, list):
            raw_list = result
        elif isinstance(result, dict):
            for k in ("value", "items", "results", "issues", "data"):
                if isinstance(result.get(k), list):
                    raw_list = result[k]; break

        return [self._normalise_issue(it) for it in raw_list
                if isinstance(it, dict)]

    def search_quality_issues(
        self,
        project_id: str,
        issue_ids: List[str],
    ) -> List[Dict]:
        """
        `POST /api/projects/{pid}/qualityIssues/search`
        body: ["issueId1", "issueId2"] (raw JSON array — string IDs)
        """
        result = self._request(
            "POST",
            f"/api/projects/{project_id}/qualityIssues/search",
            json_body=list(issue_ids),
        )
        if isinstance(result, list):
            return [self._normalise_issue(it) for it in result
                    if isinstance(it, dict)]
        return []

    @staticmethod
    def _normalise_issue(it: Dict) -> Dict:
        """Stable schema irrespective of camelCase/PascalCase server."""
        def pick(*keys, default=""):
            for k in keys:
                if k in it and it[k] not in (None, ""):
                    return it[k]
            return default

        try:
            issue_type_int = int(pick("issueType", "IssueType", "type",
                                      default=-1))
        except (TypeError, ValueError):
            issue_type_int = -1

        return {
            "id":         pick("id", "Id"),
            "segmentId":  pick("segmentId", "SegmentId", "segId"),
            "issueType":  issue_type_int,
            "issueLabel": ISSUE_TYPE_LABELS.get(
                issue_type_int, f"Type {issue_type_int}"),
            "severity":   pick("severity", "Severity", default="warning"),
            "message":    pick("message", "Message",
                               "description", "Description"),
            "sourceText": pick("sourceText", "SourceText",
                               "source", "Source"),
            "targetText": pick("targetText", "TargetText",
                               "target", "Target"),
            "suggestion": pick("suggestion", "Suggestion"),
            "raw":        it,
        }

    # ── High-level convenience: end-to-end QA ──────────────────────────────

    def run_full_qa(
        self,
        project_name: str,
        xliff_bytes: bytes,
        xliff_filename: str,
        qa_settings_id: str,
        assigned_to_id: Optional[str] = None,
        progress_cb: Optional[Callable[[str, Dict], None]] = None,
    ) -> Tuple[str, str, List[Dict]]:
        """
        Full Verifika QA chain matching the UI's behaviour:

            create_project(qaSettingsId)
              → upload_file (chunks + commit)
              → start_project (creates+assigns task)
              → list_tasks (read taskId)
              → accept_tasks
              → run_qa_check
              → wait_for_task_completion
              → get_quality_issues

        Returns (project_id, task_id, [issue dicts]).

        If `assigned_to_id` is None, we resolve the current user's GUID
        from the JWT token (or /api/Users/current as fallback).
        """
        def _emit(stage: str, payload: Dict):
            if progress_cb:
                try: progress_cb(stage, payload)
                except Exception: pass

        # 1. Create project (with QA profile baked in)
        project = self.create_project(project_name, qa_settings_id=qa_settings_id)
        project_id = project.get("id") or project.get("Id") or ""
        if not project_id:
            raise VerifikaError(f"Project create returned no id: {project}")
        _emit("project_created", project)

        # 2. Upload file
        upload = self.upload_file(project_id, xliff_bytes, xliff_filename)
        _emit("file_uploaded", upload)

        # 3. Resolve user GUID for assignment
        user_id = assigned_to_id or self.get_current_user_id()

        # 4. Start project (creates task, assigns it, "Start QA" equivalent)
        start_resp = self.start_project(project_id, assigned_to_id=user_id)
        _emit("project_started", start_resp)

        # 5. Read created task id
        tasks = self.list_tasks(project_id)
        if not tasks:
            raise VerifikaError(
                "No tasks visible after /start — server did not create one?"
            )
        task = tasks[0]
        task_id = task.get("id") or task.get("Id") or ""
        if not task_id:
            raise VerifikaError(f"Task missing id: {task}")
        _emit("task_ready", task)

        # 6. Accept tasks
        self.accept_tasks(project_id)
        _emit("tasks_accepted", {"projectId": project_id})

        # 7. Run QA check on the task
        self.run_qa_check(project_id, task_id)
        _emit("qa_check_started", {"taskId": task_id})

        # 8. Poll until complete
        final_task = self.wait_for_task_completion(
            project_id, task_id,
            progress_cb=lambda t: _emit("qa_progress", t),
        )
        _emit("qa_completed", final_task)

        # 9. Fetch issues
        issues = self.get_quality_issues(project_id, task_id)
        _emit("issues_fetched", {"count": len(issues)})

        return project_id, task_id, issues

    # ── Convenience: review URL for the rich UI ────────────────────────────

    def report_url(self, project_id: str) -> str:
        """The web UI's QA review screen — embeddable in an iframe or
        opened in a new tab."""
        return f"{self.base_url}/report/{project_id}/formal"
