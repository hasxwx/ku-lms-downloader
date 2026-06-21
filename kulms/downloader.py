"""Download resolved attachments to disk through the authenticated LMS session."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import re
import sys
from urllib import request as urllib_request

from .client import USER_AGENT, LmsClient
from .models import Attachment, Material

logger = logging.getLogger(__name__)

_CHUNK = 1 << 16  # 64 KiB
_PROGRESS_EVERY = 4 << 20  # log progress every 4 MiB


@dataclass(slots=True)
class DownloadStats:
    downloaded: int = 0
    skipped: int = 0
    links: int = 0
    failed: int = 0

    def merge(self, other: "DownloadStats") -> None:
        self.downloaded += other.downloaded
        self.skipped += other.skipped
        self.links += other.links
        self.failed += other.failed


class _HtmlResponseError(Exception):
    """The response was HTML — i.e. the URL is really an external link, not a file."""


def download_materials(
    client: LmsClient,
    materials: list[Material],
    out_root: Path,
    *,
    include_videos: bool = True,
    include_files: bool = True,
    save_links: bool = False,
    overwrite: bool = False,
) -> DownloadStats:
    stats = DownloadStats()
    for material in materials:
        attachments = [
            a
            for a in material.attachments
            if _wanted(a, include_videos=include_videos, include_files=include_files)
        ]
        if not attachments:
            continue
        folder = out_root / _safe_name(material.course.name) / _safe_name(material.title)
        folder.mkdir(parents=True, exist_ok=True)
        logger.info("[%s] %s — %d개", material.course.name, material.title, len(attachments))
        used_names: set[str] = set()
        for attachment in attachments:
            stats.merge(
                _download_one(
                    client,
                    attachment,
                    folder,
                    used_names=used_names,
                    save_links=save_links,
                    overwrite=overwrite,
                )
            )
    return stats


def _wanted(attachment: Attachment, *, include_videos: bool, include_files: bool) -> bool:
    if attachment.is_media:
        return include_videos
    return include_files


def _download_one(
    client: LmsClient,
    attachment: Attachment,
    folder: Path,
    *,
    used_names: set[str],
    save_links: bool,
    overwrite: bool,
) -> DownloadStats:
    stats = DownloadStats()
    if attachment.source_type == "link":
        if save_links:
            _write_link_file(folder, attachment, used_names)
        else:
            logger.info("  - (링크) %s -> %s", attachment.file_name, attachment.download_url)
        stats.links += 1
        return stats

    target = folder / _unique_name(_safe_name(attachment.file_name), used_names)
    try:
        result = _stream_to_file(client, attachment, target, overwrite=overwrite)
    except _HtmlResponseError:
        # Looked like a file but the server returned HTML → treat as external link.
        used_names.discard(target.name)
        if save_links:
            _write_link_file(folder, attachment, used_names)
        else:
            logger.info("  - (링크) %s -> %s", attachment.file_name, attachment.download_url)
        stats.links += 1
        return stats
    except Exception as exc:  # noqa: BLE001 — surface, keep going
        used_names.discard(target.name)
        logger.warning("  ! 다운로드 실패: %s (%s)", attachment.file_name, exc)
        stats.failed += 1
        return stats

    if result == "skipped":
        logger.info("  = (이미 있음) %s", target.name)
        stats.skipped += 1
    else:
        logger.info("  + %s", target.name)
        stats.downloaded += 1
    return stats


def _stream_to_file(client: LmsClient, attachment: Attachment, target: Path, *, overwrite: bool) -> str:
    req = urllib_request.Request(attachment.download_url, method="GET")
    req.add_header("User-Agent", USER_AGENT)
    if attachment.download_referer:
        req.add_header("Referer", attachment.download_referer)

    response = client.open(req, timeout=300)
    try:
        content_type = response.headers.get("Content-Type", "")
        if "text/html" in content_type:
            response.read()
            if attachment.media_kind is None:
                # A Canvas "file" that is really an external-link redirect → save as link.
                raise _HtmlResponseError(content_type)
            # Expected media/binary but got an HTML page (expired link or kucom
            # hotlink rejection). Fail loudly instead of writing HTML into a .mp4.
            raise RuntimeError(f"서버가 파일 대신 HTML을 반환했습니다 ({content_type})")

        total = _content_length(response)
        if not overwrite and target.exists() and total is not None and target.stat().st_size == total:
            response.read()
            return "skipped"

        tmp = target.with_suffix(target.suffix + ".part")
        written = 0
        next_mark = _PROGRESS_EVERY
        try:
            with tmp.open("wb") as handle:
                while chunk := response.read(_CHUNK):
                    handle.write(chunk)
                    written += len(chunk)
                    if written >= next_mark:
                        _print_progress(target.name, written, total)
                        next_mark += _PROGRESS_EVERY
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        if written >= _PROGRESS_EVERY:
            # Always terminate the \r progress line so the next log line isn't garbled,
            # even when Content-Length was unknown (common for kucom progressive media).
            _print_progress(target.name, written, total, final=True)
        tmp.replace(target)
        return "downloaded"
    finally:
        close = getattr(response, "close", None)
        if close:
            close()


def _content_length(response) -> int | None:
    raw = response.headers.get("Content-Length")
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _print_progress(name: str, written: int, total: int | None, *, final: bool = False) -> None:
    if total:
        pct = written / total * 100
        line = f"    {name}: {written / 1048576:.1f}/{total / 1048576:.1f} MiB ({pct:.0f}%)"
    else:
        line = f"    {name}: {written / 1048576:.1f} MiB"
    end = "\n" if final else "\r"
    sys.stderr.write(line + (" " * 4) + end)
    sys.stderr.flush()


def _write_link_file(folder: Path, attachment: Attachment, used_names: set[str]) -> None:
    base = _unique_name(_safe_name(attachment.file_name) + ".url.txt", used_names)
    (folder / base).write_text(
        f"{attachment.file_name}\n{attachment.download_url}\n", encoding="utf-8"
    )
    logger.info("  - (링크 저장) %s", base)


def _unique_name(name: str, used: set[str]) -> str:
    if name not in used:
        used.add(name)
        return name
    stem, dot, ext = name.partition(".")
    counter = 2
    while True:
        candidate = f"{stem} ({counter}){dot}{ext}" if dot else f"{name} ({counter})"
        if candidate not in used:
            used.add(candidate)
            return candidate
        counter += 1


def _safe_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name or "").strip(" .")
    return cleaned or "untitled"
