from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Course:
    external_id: str
    name: str
    term: str = "unknown"


@dataclass(slots=True)
class Attachment:
    external_id: str
    file_name: str
    download_url: str
    source_type: str = "file"  # "file" | "link"
    media_kind: str | None = None  # None | "video" | "audio"
    download_referer: str | None = None  # 다운로드 시 필요한 Referer (예: kucom CDN 핫링크 보호)
    mime_type: str | None = None

    @property
    def is_media(self) -> bool:
        return self.media_kind in {"video", "audio"}


@dataclass(slots=True)
class Material:
    """A logical group of attachments: a module(주차), an assignment(과제), or a board post(게시판)."""

    course: Course
    external_id: str
    title: str
    kind: str  # "module" | "assignment" | "board"
    source_url: str | None = None
    attachments: list[Attachment] = field(default_factory=list)
