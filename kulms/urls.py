from __future__ import annotations

from dataclasses import dataclass
import re
from urllib import parse

DEFAULT_BASE_URL = "https://mylms.korea.ac.kr"

_COURSE_RE = re.compile(r"/courses/(\d+)")
_ASSIGNMENT_RE = re.compile(r"/assignments/(\d+)")


@dataclass(slots=True)
class LmsTarget:
    base_url: str
    course_id: str
    scope: str = "all"  # "all" | "modules" | "assignments" | "board"
    assignment_id: str | None = None


def parse_lms_url(raw: str, *, default_base_url: str = DEFAULT_BASE_URL) -> LmsTarget:
    """Parse a pasted LMS link (or bare course id) into a download target.

    Accepts:
      - https://lms.korea.ac.kr/courses/12345                 -> scope=all
      - https://lms.korea.ac.kr/courses/12345/modules         -> scope=modules
      - https://lms.korea.ac.kr/courses/12345/assignments/678 -> scope=assignments, assignment_id=678
      - lms.korea.ac.kr/courses/12345 (no scheme)             -> scheme assumed https
      - 12345 (bare course id)                                -> uses default_base_url
    """
    text = (raw or "").strip()
    if not text:
        raise ValueError("LMS 링크가 비어 있습니다.")

    if text.isdigit():
        return LmsTarget(base_url=default_base_url.rstrip("/"), course_id=text, scope="all")

    if "://" not in text:
        text = "https://" + text

    parsed = parse.urlparse(text)
    if not parsed.netloc:
        raise ValueError(f"LMS 링크를 해석할 수 없습니다: {raw!r}")
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    match = _COURSE_RE.search(parsed.path)
    if not match:
        raise ValueError(
            f"링크에서 course id(/courses/<숫자>)를 찾지 못했습니다: {raw!r}\n"
            "강의 홈 · 주차학습 · 과제 페이지의 URL을 붙여넣어 주세요."
        )
    course_id = match.group(1)

    path = parsed.path
    scope = "all"
    assignment_id: str | None = None
    if "/assignments" in path:
        scope = "assignments"
        am = _ASSIGNMENT_RE.search(path)
        if am:
            assignment_id = am.group(1)
    elif "/modules" in path or "/external_tools" in path or "/pages" in path:
        scope = "modules"

    return LmsTarget(
        base_url=base_url,
        course_id=course_id,
        scope=scope,
        assignment_id=assignment_id,
    )
