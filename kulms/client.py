"""Korea University LMS client — authenticate, resolve attachments, expose an authenticated opener.

This is a trimmed port of the LMSintegration connector: it keeps the KSSO single
sign-on flow, the LearningX / board bootstrap, the Canvas + LearningX module
attachment resolution, and the kucom video URL resolver. Everything specific to
Notion / Google Drive / state tracking has been removed. Standard library only.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from http.cookiejar import CookieJar
from html.parser import HTMLParser
import json
import logging
from pathlib import PurePosixPath
import re
import time
from urllib import error, parse, request

from .models import Attachment, Course, Material

logger = logging.getLogger(__name__)


KSSO_AUTH_URL = (
    "https://ksso.korea.ac.kr/svc/tk/Auth.do?"
    "id=lms&ac=Y&ifa=N&RelayState="
    "https%3A%2F%2Flms.korea.ac.kr%2Fxn-sso%2Fgw-cb.php%3Ffrom%3D%26site%3D%26"
    "login_type%3Dstandalone%26return_url%3Dhttps%253A%252F%252Flms.korea.ac.kr%252Flogin%252Fcallback"
)
KSSO_USER_TYPE_CHECK_URL = "https://ksso.korea.ac.kr/logincheck/UserTypeCheck.do"
KSSO_OTP_CHECK_URL = "https://ksso.korea.ac.kr/logincheck/OTPCheck.do"
KSSO_LOGIN_URL = "https://ksso.korea.ac.kr/Login.do"
LMS_FROM_CC_PATH = "/learningx/login/from_cc"
CANVAS_LOGIN_PATH = "/login/canvas"

USER_AGENT = "kulms/0.1 (+https://github.com/)"


class _HiddenInputParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.inputs: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "input":
            return
        attr_map = dict(attrs)
        name = attr_map.get("name")
        if not name:
            return
        self.inputs[name] = attr_map.get("value", "") or ""


@dataclass(slots=True)
class LmsClient:
    base_url: str
    username: str
    password: str
    login_user_id: str = ""
    timeout_sec: int = 20

    _cookie_jar: CookieJar = field(default_factory=CookieJar, init=False, repr=False)
    _opener: request.OpenerDirector | None = field(default=None, init=False, repr=False)
    _authenticated: bool = field(default=False, init=False, repr=False)
    _csrf_token: str = field(default="", init=False, repr=False)
    _course_cache: dict[str, Course] = field(default_factory=dict, init=False, repr=False)
    _learningx_bootstrapped: set[str] = field(default_factory=set, init=False, repr=False)
    _learningx_referers: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _board_bootstrapped: set[str] = field(default_factory=set, init=False, repr=False)
    _board_referers: dict[str, str] = field(default_factory=dict, init=False, repr=False)

    # ----------------------------------------------------------------- public API

    def get_course(self, course_id: str) -> Course:
        if course_id in self._course_cache:
            return self._course_cache[course_id]
        details = self._get_json_with_fallback(
            primary_path=f"/api/v1/courses/{course_id}",
            fallback_path=f"/learningx/api/v1/courses/{course_id}/settings",
            query={"role": "1"},
        )
        name = ""
        term = ""
        if isinstance(details, dict):
            name = self._as_str(details.get("name") or details.get("course_name")) or ""
            term_info = details.get("term")
            if isinstance(term_info, dict):
                term = self._as_str(term_info.get("name")) or ""
            term = term or self._as_str(details.get("term_name")) or ""
        course = Course(
            external_id=str(course_id),
            name=name or f"Course {course_id}",
            term=term or "unknown",
        )
        self._course_cache[course_id] = course
        return course

    def fetch_module_materials(self, course_id: str) -> list[Material]:
        """주차학습 영상 + 강의자료 첨부 (modules)."""
        course = self.get_course(course_id)
        try:
            payload = self._get_json_with_fallback(
                primary_path=f"/learningx/api/v1/courses/{course_id}/modules",
                fallback_path=f"/api/v1/courses/{course_id}/modules",
                query={"include_detail": "true"},
            )
        except RuntimeError:
            payload = self._get_canvas_modules_with_items(course_id)
        if not isinstance(payload, list):
            return []

        materials: list[Material] = []
        for module in payload:
            if not isinstance(module, dict):
                continue
            module_id = module.get("module_id") or module.get("id")
            if module_id is None:
                continue
            module_items = module.get("module_items")
            if not isinstance(module_items, list):
                module_items = module.get("items")
            if not isinstance(module_items, list):
                module_items = []
            attachments = self._extract_attachments(module_items)
            if not attachments:
                continue
            materials.append(
                Material(
                    course=course,
                    external_id=f"module:{module_id}",
                    title=self._as_str(module.get("title") or module.get("name")) or f"Module {module_id}",
                    kind="module",
                    source_url=self._absolute_url(f"/courses/{course_id}/modules#{module_id}"),
                    attachments=attachments,
                )
            )
        return materials

    def fetch_assignment_materials(self, course_id: str, assignment_id: str | None = None) -> list[Material]:
        """과제 첨부 (assignments)."""
        course = self.get_course(course_id)
        grouped_payload = self._get_json(
            primary_path=f"/api/v1/courses/{course_id}/assignment_groups",
            query={
                "include[]": "assignments",
                "override_assignment_dates": "true",
                "per_page": "100",
            },
        )
        payload = self._flatten_assignment_groups(grouped_payload)
        if not payload:
            fallback = self._get_json_with_fallback(
                primary_path=f"/api/v1/courses/{course_id}/assignments",
                fallback_path=f"/learningx/api/v1/courses/{course_id}/assignments",
                query={"per_page": "100"},
            )
            payload = fallback if isinstance(fallback, list) else []

        materials: list[Material] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            external_id = str(item.get("id") or item.get("assignment_id") or "").strip()
            title = str(item.get("name") or item.get("title") or "").strip()
            if not external_id or not title:
                continue
            if assignment_id and external_id != str(assignment_id):
                continue
            attachments = self._extract_assignment_attachments(course_id, item)
            if not attachments:
                continue
            materials.append(
                Material(
                    course=course,
                    external_id=f"assignment:{external_id}",
                    title=title,
                    kind="assignment",
                    source_url=self._as_str(item.get("html_url") or item.get("url")),
                    attachments=attachments,
                )
            )
        return materials

    def fetch_board_materials(self, course_id: str) -> list[Material]:
        """게시판(공지/자료실) 첨부."""
        course = self.get_course(course_id)
        materials: list[Material] = []
        try:
            boards = self._fetch_course_boards(course_id)
        except RuntimeError:
            return materials
        for board in boards:
            board_id = self._as_str(board.get("board_id") or board.get("id"))
            if not board_id:
                continue
            page = 1
            while True:
                try:
                    posts = self._fetch_board_posts(course_id, board_id, page=page)
                except RuntimeError:
                    break
                if not posts:
                    break
                for post in posts:
                    post_id = self._as_str(post.get("post_id") or post.get("id"))
                    if not post_id:
                        continue
                    if not (post.get("attachment_count") or 0):
                        continue
                    detail = self._fetch_board_post_detail(course_id, board_id, post_id)
                    if detail is None:
                        continue
                    attachments = self._extract_attachments_from_board_post(detail)
                    if not attachments:
                        continue
                    materials.append(
                        Material(
                            course=course,
                            external_id=f"board_post:{post_id}",
                            title=self._as_str(post.get("title") or post.get("subject")) or f"Post {post_id}",
                            kind="board",
                            source_url=self._as_str(detail.get("post_url"))
                            or self._absolute_url(
                                f"/learningx/lti/learningx_board/boards/{board_id}/posts/{post_id}"
                            ),
                            attachments=attachments,
                        )
                    )
                page += 1
        return materials

    def open(self, req: request.Request, timeout: int | None = None):
        """Open a request through the authenticated opener (used by the downloader)."""
        self._ensure_authenticated()
        opener = self._get_opener()
        return opener.open(req, timeout=self.timeout_sec if timeout is None else timeout)

    # --------------------------------------------------------------- board access

    def _fetch_course_boards(self, course_id: str) -> list[dict[str, object]]:
        self._ensure_authenticated()
        self._prepare_board_course_access(course_id)
        referer = self._board_referers.get(course_id, self.base_url.rstrip("/") + "/")
        url = self._absolute_url(f"/learningx/api/v1/learningx_board/courses/{course_id}/boards")
        response = self._send_request(url=url, method="GET", referer=referer)
        payload = json.loads(response.read().decode("utf-8", errors="replace") or "[]")
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []

    def _fetch_board_posts(self, course_id: str, board_id: str, page: int = 1) -> list[dict[str, object]]:
        self._ensure_authenticated()
        self._prepare_board_course_access(course_id)
        referer = self._absolute_url(f"/learningx/lti/learningx_board/boards/{board_id}")
        url = self._absolute_url(
            f"/learningx/api/v1/learningx_board/courses/{course_id}/boards/{board_id}/posts",
            query={"page": str(page), "filter": "title", "keyword": ""},
        )
        response = self._send_request(url=url, method="GET", referer=referer)
        payload = json.loads(response.read().decode("utf-8", errors="replace") or "{}")
        if isinstance(payload, dict):
            posts = payload.get("posts") or payload.get("items") or payload.get("data")
            if isinstance(posts, list):
                return [item for item in posts if isinstance(item, dict)]
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []

    def _fetch_board_post_detail(self, course_id: str, board_id: str, post_id: str) -> dict[str, object] | None:
        url = self._absolute_url(
            f"/learningx/api/v1/learningx_board/courses/{course_id}/boards/{board_id}/posts/{post_id}"
        )
        referer = self._absolute_url(f"/learningx/lti/learningx_board/boards/{board_id}")
        try:
            response = self._send_request(url=url, method="GET", referer=referer)
            detail = json.loads(response.read().decode("utf-8", errors="replace"))
            return detail if isinstance(detail, dict) else None
        except RuntimeError:
            return None

    def _extract_attachments_from_board_post(self, post: dict[str, object]) -> list[Attachment]:
        raw = post.get("attachments") or post.get("files") or post.get("attachment_list") or []
        if not isinstance(raw, list):
            return []
        attachments: list[Attachment] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            url = self._as_str(item.get("url") or item.get("download_url") or item.get("file_url"))
            if not url:
                continue
            url = self._absolute_url(url)
            external_id = self._as_str(item.get("id") or item.get("attachment_id")) or url
            file_name = self._as_str(
                item.get("filename") or item.get("file_name") or item.get("name")
            )
            if not file_name:
                file_name = PurePosixPath(parse.urlparse(url).path).name or f"file-{len(attachments) + 1}"
            attachments.append(
                Attachment(
                    external_id=str(external_id),
                    file_name=file_name,
                    download_url=url,
                    media_kind=self._guess_media_kind(file_name),
                )
            )
        return attachments

    # --------------------------------------------------------- module attachments

    def _get_canvas_modules_with_items(self, course_id: str) -> object:
        return self._get_json(
            primary_path=f"/api/v1/courses/{course_id}/modules",
            query={"include[]": "items", "per_page": "100"},
        )

    def _extract_attachments(self, module_items: list[object]) -> list[Attachment]:
        attachments: list[Attachment] = []
        for item in module_items:
            if not isinstance(item, dict):
                continue

            resolved = self._resolve_attachment_from_module_item(item)
            if resolved:
                attachments.append(resolved)
                continue

            resolved = self._resolve_learningx_content_item(item)
            if resolved:
                attachments.append(resolved)
                continue

            raw_url = self._as_str(
                item.get("download_url")
                or item.get("file_url")
                or item.get("external_url")
                or item.get("url")
                or item.get("href")
            )
            if not raw_url:
                continue
            url = self._absolute_url(raw_url)
            external_id = self._as_str(item.get("item_id") or item.get("id") or item.get("module_item_id")) or url
            file_name = self._as_str(item.get("file_name") or item.get("title") or item.get("name"))
            if not file_name:
                file_name = PurePosixPath(parse.urlparse(url).path).name or f"file-{len(attachments) + 1}"
            attachments.append(
                Attachment(
                    external_id=external_id,
                    file_name=file_name,
                    download_url=url,
                    source_type="link",
                    media_kind=self._guess_media_kind(file_name),
                )
            )
        return attachments

    def _resolve_attachment_from_module_item(self, item: dict[str, object]) -> Attachment | None:
        item_type = self._as_str(item.get("type"))
        content_type = self._as_str(item.get("content_type"))
        content_id = self._as_str(item.get("content_id"))
        if (item_type != "File" and content_type != "attachment") or not content_id:
            return None

        file_payload = self._get_json(
            primary_path=f"/api/v1/files/{content_id}/public_url.json",
            query=None,
        )
        if not isinstance(file_payload, dict):
            return None
        public_url = self._as_str(file_payload.get("public_url"))
        if not public_url:
            return None
        file_name = (
            self._as_str(file_payload.get("display_name"))
            or self._as_str(file_payload.get("filename"))
            or self._as_str(item.get("title"))
            or f"file-{content_id}"
        )
        return Attachment(
            external_id=content_id,
            file_name=file_name,
            download_url=public_url,
            media_kind=self._guess_media_kind(file_name),
        )

    def _resolve_learningx_content_item(self, item: dict[str, object]) -> Attachment | None:
        """LearningX attendance_item 형식에서 콘텐츠(주차학습 영상/자료)를 추출한다."""
        content_data = item.get("content_data")
        if not isinstance(content_data, dict):
            return None
        icd = content_data.get("item_content_data")
        if not isinstance(icd, dict):
            return None

        content_type = self._as_str(icd.get("content_type"))
        external_id = self._as_str(icd.get("content_id") or icd.get("_id")) or ""
        if not external_id:
            return None

        # embed + website → 외부 링크
        if content_type == "embed" and self._as_str(icd.get("content_subtype")) == "website":
            weblink = self._as_str(icd.get("weblink"))
            if not weblink:
                return None
            title = self._as_str(icd.get("weblink_title") or icd.get("title") or item.get("title"))
            return Attachment(
                external_id=external_id,
                file_name=title or f"link-{external_id}",
                download_url=weblink,
                source_type="link",
            )

        # pdf, video 등 실제 콘텐츠 → kucom에서 다운로드 URL 해석
        view_url = self._as_str(icd.get("view_url"))
        if not view_url:
            return None
        file_name = self._as_str(icd.get("file_name") or icd.get("title") or item.get("title"))
        if not file_name:
            file_name = f"file-{external_id}"

        download_url = self._resolve_kucom_download_url(external_id, view_url)
        if not download_url:
            return Attachment(
                external_id=external_id,
                file_name=file_name,
                download_url=view_url,
                source_type="link",
            )

        # kucom CDN은 핫링크 보호가 있어 Referer로 kucom origin이 필요하다.
        view_parsed = parse.urlparse(view_url)
        download_referer = (
            f"{view_parsed.scheme}://{view_parsed.netloc}/" if view_parsed.netloc else None
        )
        return Attachment(
            external_id=external_id,
            file_name=file_name,
            download_url=download_url,
            source_type="file",
            # 파일명에 확장자가 없을 수 있어(upf의 "Ch.6_Ch.7") 해석된 URL로도 판별.
            media_kind=self._guess_media_kind(file_name, content_type) or self._guess_media_kind(download_url),
            download_referer=download_referer,
        )

    def _resolve_kucom_download_url(self, content_id: str, view_url: str) -> str | None:
        """kucom view_url에서 content.php XML을 조회하여 실제 다운로드 URL을 반환한다."""
        parsed = parse.urlparse(view_url)
        kucom_base = f"{parsed.scheme}://{parsed.netloc}"
        content_xml_url = f"{kucom_base}/viewer/ssplayer/uniplayer_support/content.php?content_id={content_id}"

        opener = self._get_opener()
        req = request.Request(content_xml_url, method="GET")
        req.add_header("User-Agent", USER_AGENT)
        req.add_header("Referer", view_url)
        try:
            resp = opener.open(req, timeout=30)
            xml_text = resp.read().decode("utf-8", errors="replace")
        except (error.URLError, error.HTTPError, TimeoutError, OSError, ValueError):
            return None

        match = re.search(r"<content_download_uri>(.*?)</content_download_uri>", xml_text, re.S)
        if match:
            raw_path = match.group(1).replace("&amp;", "&").strip()
            if raw_path:
                return f"{kucom_base}{raw_path}" if raw_path.startswith("/") else raw_path
        # upf(슬라이드+화면녹화 프레젠테이션) 형식: story의 main_media + service_root의 progressive URI
        return self._build_upf_media_url(xml_text)

    @staticmethod
    def _build_upf_media_url(xml_text: str) -> str | None:
        # `<main_media ...>` 만 매칭(`<main_media_list ...>` 래퍼는 제외).
        media_match = re.search(r"<main_media(?:\s[^>]*)?>(.*?)</main_media>", xml_text, re.S)
        if not media_match:
            return None
        media_file = media_match.group(1).strip()
        if not media_file:
            return None
        uri_template: str | None = None
        for target in ('target="all"', 'target="mobile"', ""):
            pattern = rf'<media_uri[^>]*method="progressive"[^>]*{target}[^>]*>(.*?)</media_uri>'
            m = re.search(pattern, xml_text, re.S)
            if m:
                uri_template = m.group(1).strip()
                break
        if not uri_template:
            return None
        return uri_template.replace("[MEDIA_FILE]", media_file)

    def _extract_assignment_attachments(self, course_id: str, item: dict[str, object]) -> list[Attachment]:
        attachments: list[Attachment] = []
        description = self._as_str(item.get("description")) or ""
        if not description:
            return attachments

        seen_urls: set[str] = set()
        seen_file_ids: set[str] = set()

        def _extract_file_id(url: str) -> str | None:
            match = re.search(r"/files/(\d+)", url)
            return match.group(1) if match else None

        for endpoint in re.findall(r'data-api-endpoint="([^"]+)"', description):
            endpoint_path = endpoint.replace(self.base_url.rstrip("/"), "")
            if f"/api/v1/courses/{course_id}/files/" not in endpoint_path:
                continue
            try:
                file_payload = self._get_json(primary_path=endpoint_path, query=None)
            except RuntimeError:
                continue
            if not isinstance(file_payload, dict):
                continue
            download_url = self._as_str(file_payload.get("url"))
            if not download_url:
                continue
            absolute_url = self._absolute_url(download_url)
            file_id = _extract_file_id(absolute_url)
            if file_id:
                seen_file_ids.add(file_id)
            if absolute_url in seen_urls:
                continue
            seen_urls.add(absolute_url)
            file_name = (
                self._as_str(file_payload.get("display_name"))
                or self._as_str(file_payload.get("filename"))
                or f"file-{len(attachments) + 1}"
            )
            attachments.append(
                Attachment(
                    external_id=self._as_str(file_payload.get("id")) or absolute_url,
                    file_name=file_name,
                    download_url=absolute_url,
                    mime_type=self._as_str(file_payload.get("content-type")),
                    media_kind=self._guess_media_kind(file_name),
                )
            )

        for src in re.findall(r'<img[^>]+src="([^"]+)"', description):
            absolute_url = self._absolute_url(src)
            file_id = _extract_file_id(absolute_url)
            if file_id and file_id in seen_file_ids:
                continue
            if absolute_url in seen_urls:
                continue
            seen_urls.add(absolute_url)
            file_name = PurePosixPath(parse.urlparse(absolute_url).path).name or f"image-{len(attachments) + 1}"
            attachments.append(
                Attachment(
                    external_id=absolute_url,
                    file_name=file_name,
                    download_url=absolute_url,
                    source_type="link",
                )
            )
        return attachments

    @staticmethod
    def _flatten_assignment_groups(payload: object | None) -> list[dict]:
        if not isinstance(payload, list):
            return []
        assignments: list[dict] = []
        for group in payload:
            if not isinstance(group, dict):
                continue
            group_assignments = group.get("assignments")
            if not isinstance(group_assignments, list):
                continue
            for assignment in group_assignments:
                if isinstance(assignment, dict):
                    assignments.append(assignment)
        return assignments

    @staticmethod
    def _guess_media_kind(file_name: str | None, content_type: str | None = None) -> str | None:
        """파일명/LMS content_type으로 영상·음성 여부를 분류한다."""
        ct = (content_type or "").lower()
        if ct in {"movie", "video"} or ct.startswith("video"):
            return "video"
        if ct in {"audio", "sound"} or ct.startswith("audio"):
            return "audio"
        name = (file_name or "").lower()
        if name.endswith(
            (".mp4", ".mkv", ".mov", ".avi", ".wmv", ".m4v", ".flv", ".webm", ".ts", ".mpg", ".mpeg")
        ):
            return "video"
        if name.endswith((".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus")):
            return "audio"
        return None

    # ----------------------------------------------------------- authentication

    def _ensure_authenticated(self) -> None:
        if self._authenticated:
            return
        if not (self.username and self.password):
            raise RuntimeError("LMS credentials are required for KSSO session login")

        login_page_html = self._open_text(KSSO_AUTH_URL, referer=None)
        auth_tokens = self._parse_hidden_inputs(login_page_html)
        l_token = auth_tokens.get("l_token", "")
        c_token = auth_tokens.get("c_token", "")
        if not l_token or not c_token:
            raise RuntimeError("Unable to extract KSSO login tokens from the auth page")

        user_type_response = self._post_json_form(
            KSSO_USER_TYPE_CHECK_URL,
            {
                "l_token": l_token,
                "c_token": c_token,
                "user_timezone_offset": "",
                "user_id": "",
                "one_id": self.username,
                "user_password": self.password,
            },
            referer=KSSO_AUTH_URL,
        )
        c_token = self._as_str(user_type_response.get("c_token")) or c_token
        login_user_id = (
            self.login_user_id
            or self._first_user_type_id(user_type_response)
            or self.username
        )

        self._post_json_form(
            KSSO_OTP_CHECK_URL,
            {"type": "NeedOTPCheck", "emp_no": login_user_id},
            referer=KSSO_AUTH_URL,
        )

        login_response_url = self._post_form(
            KSSO_LOGIN_URL,
            {
                "l_token": l_token,
                "c_token": c_token,
                "user_timezone_offset": "-540",
                "user_id": login_user_id,
                "one_id": self.username,
                "user_password": self.password,
            },
            referer=KSSO_AUTH_URL,
        )

        result_token = self._extract_result_token(login_response_url)
        if not result_token:
            raise RuntimeError(
                "Unable to extract the SSO result token after KSSO login "
                "(아이디/비밀번호를 확인하세요)"
            )

        bridge_url = self._absolute_url(LMS_FROM_CC_PATH, query={"result": result_token})
        bridge_html = self._open_text(bridge_url, referer="https://lms.korea.ac.kr/")
        bridge_inputs = self._parse_hidden_inputs(bridge_html)
        canvas_form = self._build_canvas_login_payload_from_html(bridge_html, bridge_inputs)
        if not canvas_form:
            raise RuntimeError("Unable to extract the Canvas bridge login form")

        self._post_form(self._absolute_url(CANVAS_LOGIN_PATH), canvas_form, referer=bridge_url)
        home_html = self._open_text(self._absolute_url("/?login_success=1"), referer=bridge_url)
        self._csrf_token = self._extract_csrf_token(home_html)
        self._authenticated = True

    @staticmethod
    def _build_canvas_login_payload(bridge_inputs: dict[str, str]) -> dict[str, str]:
        unique_id = bridge_inputs.get("pseudonym_session[unique_id]", "")
        session_password = bridge_inputs.get("pseudonym_session[password]", "")
        if not unique_id or not session_password:
            return {}
        return {
            "utf8": bridge_inputs.get("utf8", "✓"),
            "redirect_to_ssl": bridge_inputs.get("redirect_to_ssl", "1"),
            "after_login_url": bridge_inputs.get("after_login_url", ""),
            "pseudonym_session[unique_id]": unique_id,
            "pseudonym_session[password]": session_password,
            "pseudonym_session[remember_me]": bridge_inputs.get("pseudonym_session[remember_me]", "0"),
        }

    def _build_canvas_login_payload_from_html(
        self, bridge_html: str, bridge_inputs: dict[str, str]
    ) -> dict[str, str]:
        merged_inputs = dict(bridge_inputs)
        if not merged_inputs.get("pseudonym_session[password]"):
            merged_inputs["pseudonym_session[password]"] = self._decrypt_canvas_bridge_password(bridge_html)
        return self._build_canvas_login_payload(merged_inputs)

    # --------------------------------------------------- LearningX / board bootstrap

    def _prepare_learningx_course_access(self, course_id: str) -> None:
        if course_id in self._learningx_bootstrapped:
            return

        course_url = self._absolute_url(f"/courses/{course_id}")
        course_html = self._open_text(course_url, referer=self.base_url.rstrip("/") + "/")
        tool_ids = self._extract_external_tool_ids(course_html, course_id) or ["1"]

        for tool_id in tool_ids:
            ext_url = self._absolute_url(f"/courses/{course_id}/external_tools/{tool_id}")
            ext_html = self._open_text(ext_url, referer=course_url)
            modulebuilder_form = self._extract_form(ext_html, "learningx/lti/modulebuilder")
            if not modulebuilder_form:
                continue
            action_url, payload = modulebuilder_form
            post_url = action_url if action_url.startswith("http") else self._absolute_url(action_url)
            response = self._send_request(
                url=post_url,
                method="POST",
                referer=ext_url,
                data=parse.urlencode(payload).encode("utf-8"),
                content_type="application/x-www-form-urlencoded",
            )
            response_html = response.read().decode("utf-8", errors="replace")
            token = self._extract_csrf_token(response_html)
            if token:
                self._csrf_token = token
            self._learningx_referers[course_id] = self._absolute_url("/learningx/lti/modulebuilder")
            self._learningx_bootstrapped.add(course_id)
            return

    def _prepare_board_course_access(self, course_id: str) -> None:
        if course_id in self._board_bootstrapped:
            return
        self._ensure_authenticated()

        ext_url = self._absolute_url(f"/courses/{course_id}/external_tools/5")
        course_ref = self._absolute_url(f"/courses/{course_id}")
        html = self._open_text(ext_url, referer=course_ref)
        board_form = self._extract_form(html, "learningx/lti/learningx_board/boards")
        if not board_form:
            self._board_bootstrapped.add(course_id)
            return

        action_url, payload = board_form
        post_url = action_url if action_url.startswith("http") else self._absolute_url(action_url)
        response = self._send_request(
            url=post_url,
            method="POST",
            referer=ext_url,
            data=parse.urlencode(payload).encode("utf-8"),
            content_type="application/x-www-form-urlencoded",
        )
        response_html = response.read().decode("utf-8", errors="replace")
        token = self._extract_csrf_token(response_html)
        if token:
            self._csrf_token = token

        board_id = self._extract_board_id_from_html(response_html)
        if board_id:
            self._board_referers[course_id] = self._absolute_url(
                f"/learningx/lti/learningx_board/boards/{board_id}"
            )
        else:
            self._board_referers[course_id] = self._absolute_url("/learningx/lti/learningx_board/boards")
        self._board_bootstrapped.add(course_id)

    @staticmethod
    def _extract_external_tool_ids(html: str, course_id: str) -> list[str]:
        pattern = rf"/courses/{re.escape(course_id)}/external_tools/(\d+)"
        return list(dict.fromkeys(re.findall(pattern, html)))

    @staticmethod
    def _extract_form(html: str, action_contains: str) -> tuple[str, dict[str, str]] | None:
        match = re.search(
            rf'<form[^>]*action="([^"]*{re.escape(action_contains)}[^"]*)"[^>]*>(.*?)</form>',
            html,
            re.I | re.S,
        )
        if not match:
            return None
        action = match.group(1)
        inputs = re.findall(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', match.group(2), re.I)
        if not inputs:
            return None
        return action, {name: value for name, value in inputs}

    @staticmethod
    def _extract_board_id_from_html(html: str) -> str | None:
        for pattern in (
            r'data-board_id="(\d+)"',
            r"/learningx/lti/learningx_board/boards/(\d+)",
        ):
            match = re.search(pattern, html)
            if match:
                return match.group(1)
        return None

    @staticmethod
    def _extract_learningx_course_id(path_or_url: str) -> str | None:
        match = re.search(r"/learningx/api/v1/courses/(\d+)/", path_or_url)
        return match.group(1) if match else None

    # ------------------------------------------------------------------ transport

    def _get_json_with_fallback(
        self, primary_path: str, fallback_path: str, query: dict[str, str] | None = None
    ) -> object:
        payload = self._get_json(primary_path=primary_path, query=query)
        if payload is not None:
            return payload
        return self._get_json(primary_path=fallback_path, query=query)

    def _get_json(self, primary_path: str, query: dict[str, str] | None = None) -> object | None:
        if not self.base_url:
            return None
        self._ensure_authenticated()
        course_id = self._extract_learningx_course_id(primary_path)
        if course_id:
            self._prepare_learningx_course_access(course_id)
        url = self._absolute_url(primary_path, query=query)
        referer = (
            self._learningx_referers.get(course_id or "") if course_id else None
        ) or self.base_url.rstrip("/") + "/"
        response = self._send_request(url=url, method="GET", referer=referer)
        body = response.read().decode("utf-8", errors="replace")
        if not body:
            return None
        return json.loads(body)

    def _open_text(self, url: str, referer: str | None) -> str:
        response = self._send_request(url=url, method="GET", referer=referer)
        return response.read().decode("utf-8", errors="replace")

    def _post_form(self, url: str, payload: dict[str, str], referer: str | None) -> str:
        response = self._send_request(
            url=url,
            method="POST",
            referer=referer,
            data=parse.urlencode(payload).encode("utf-8"),
            content_type="application/x-www-form-urlencoded",
        )
        response.read()
        return response.geturl()

    def _post_json_form(self, url: str, payload: dict[str, str], referer: str | None) -> dict[str, object]:
        response = self._send_request(
            url=url,
            method="POST",
            referer=referer,
            data=parse.urlencode(payload).encode("utf-8"),
            content_type="application/x-www-form-urlencoded",
        )
        text = response.read().decode("utf-8", errors="replace")
        return json.loads(text) if text else {}

    def _send_request(
        self,
        url: str,
        method: str,
        referer: str | None,
        data: bytes | None = None,
        content_type: str | None = None,
    ):
        opener = self._get_opener()
        req = request.Request(url=url, data=data, method=method)
        req.add_header("User-Agent", USER_AGENT)
        req.add_header("Accept", "application/json, text/html, */*")
        if referer:
            req.add_header("Referer", referer)
        if content_type:
            req.add_header("Content-Type", content_type)
        if self._csrf_token:
            req.add_header("X-CSRF-Token", self._csrf_token)

        api_prefix = self.base_url.rstrip("/") + "/api/"
        learningx_api_prefix = self.base_url.rstrip("/") + "/learningx/api/"
        if url.startswith(api_prefix) or url.startswith(learningx_api_prefix):
            req.add_header("X-Requested-With", "XMLHttpRequest")
        if url.startswith(learningx_api_prefix):
            xn_api_token = next(
                (c.value for c in self._cookie_jar if c.name == "xn_api_token"), None
            )
            if xn_api_token:
                req.add_header("Authorization", f"Bearer {xn_api_token}")

        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                return opener.open(req, timeout=self.timeout_sec)
            except error.HTTPError as exc:
                if exc.code in (401, 403, 404):
                    raise RuntimeError(f"LMS request rejected: {url} ({exc.code})") from exc
                raise RuntimeError(f"LMS request failed: {url} ({exc.code})") from exc
            except error.URLError as exc:
                raise RuntimeError(f"Unable to connect LMS endpoint: {url}") from exc
            except OSError as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(1 + attempt)
        raise RuntimeError(f"LMS connection reset: {url}") from last_exc

    def _get_opener(self) -> request.OpenerDirector:
        if self._opener is None:
            self._opener = request.build_opener(request.HTTPCookieProcessor(self._cookie_jar))
        return self._opener

    def _absolute_url(self, path_or_url: str, query: dict[str, str] | None = None) -> str:
        base = self.base_url.rstrip("/")
        parsed = parse.urlparse(path_or_url)
        if parsed.scheme and parsed.netloc:
            url = path_or_url
        else:
            cleaned = path_or_url if path_or_url.startswith("/") else f"/{path_or_url}"
            url = f"{base}{cleaned}"
        if query:
            qs = parse.urlencode(query, doseq=True)
            return f"{url}&{qs}" if "?" in url else f"{url}?{qs}"
        return url

    # ----------------------------------------------------------- parsing helpers

    @staticmethod
    def _parse_hidden_inputs(html: str) -> dict[str, str]:
        parser = _HiddenInputParser()
        parser.feed(html)
        return parser.inputs

    @staticmethod
    def _extract_result_token(url: str) -> str | None:
        query = parse.parse_qs(parse.urlparse(url).query)
        values = query.get("result")
        return values[0] if values else None

    @staticmethod
    def _extract_csrf_token(html: str) -> str:
        for pattern in (r'name="csrf-token"\s+content="([^"]+)"', r'"_csrf_token":"([^"]+)"'):
            match = re.search(pattern, html)
            if match:
                return match.group(1)
        return ""

    @staticmethod
    def _first_user_type_id(payload: dict[str, object]) -> str | None:
        types = payload.get("types")
        if not isinstance(types, list) or not types:
            return None
        first = types[0]
        if not isinstance(first, dict):
            return None
        value = first.get("user_id")
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    # --------------------------------------------------- RSA bridge password decrypt

    @staticmethod
    def _decrypt_canvas_bridge_password(html: str) -> str:
        match = re.search(r'window\.loginCryption\("([^"]+)",\s*"([^"]+)"\)', html)
        if not match:
            return ""
        encrypted_b64 = match.group(1)
        pem = match.group(2).replace("\\n", "\n")
        return LmsClient._rsa_pkcs1_v15_decrypt_b64(encrypted_b64, pem)

    @staticmethod
    def _rsa_pkcs1_v15_decrypt_b64(ciphertext_b64: str, private_key_pem: str) -> str:
        ciphertext = base64.b64decode(ciphertext_b64)
        n, d = LmsClient._parse_rsa_private_key(private_key_pem)
        c = int.from_bytes(ciphertext, "big")
        m = pow(c, d, n)
        byte_length = (n.bit_length() + 7) // 8
        block = m.to_bytes(byte_length, "big")
        if len(block) < 11 or block[0] != 0 or block[1] != 2:
            return ""
        separator_index = block.find(b"\x00", 2)
        if separator_index == -1:
            return ""
        return block[separator_index + 1:].decode("utf-8", errors="replace")

    @staticmethod
    def _parse_rsa_private_key(private_key_pem: str) -> tuple[int, int]:
        body = private_key_pem.replace("-----BEGIN RSA PRIVATE KEY-----", "")
        body = body.replace("-----END RSA PRIVATE KEY-----", "")
        body = "".join(body.split())
        der = base64.b64decode(body)
        sequence, _ = LmsClient._read_asn1_tlv(der, 0, expected_tag=0x30)
        inner_offset = 0
        _, inner_offset = LmsClient._read_asn1_integer(sequence, inner_offset)
        n, inner_offset = LmsClient._read_asn1_integer(sequence, inner_offset)
        _, inner_offset = LmsClient._read_asn1_integer(sequence, inner_offset)
        d, inner_offset = LmsClient._read_asn1_integer(sequence, inner_offset)
        return n, d

    @staticmethod
    def _read_asn1_tlv(data: bytes, offset: int, expected_tag: int | None = None) -> tuple[bytes, int]:
        tag = data[offset]
        if expected_tag is not None and tag != expected_tag:
            raise ValueError(f"Unexpected ASN.1 tag: {tag}")
        offset += 1
        length_byte = data[offset]
        offset += 1
        if length_byte & 0x80:
            length_size = length_byte & 0x7F
            length = int.from_bytes(data[offset : offset + length_size], "big")
            offset += length_size
        else:
            length = length_byte
        value = data[offset : offset + length]
        return value, offset + length

    @staticmethod
    def _read_asn1_integer(data: bytes, offset: int) -> tuple[int, int]:
        value, offset = LmsClient._read_asn1_tlv(data, offset, expected_tag=0x02)
        return int.from_bytes(value, "big", signed=False), offset

    @staticmethod
    def _as_str(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None
