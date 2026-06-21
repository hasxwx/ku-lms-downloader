from __future__ import annotations

from kulms.downloader import _safe_name, _unique_name, _wanted
from kulms.models import Attachment


def test_safe_name_strips_illegal_chars():
    assert _safe_name('a/b:c*?"<>|d') == "a_b_c_d"
    assert _safe_name("   ...  ") == "untitled"
    assert _safe_name("정상 파일.pdf") == "정상 파일.pdf"


def test_unique_name_disambiguates_collisions():
    used: set[str] = set()
    assert _unique_name("a.pdf", used) == "a.pdf"
    assert _unique_name("a.pdf", used) == "a (2).pdf"
    assert _unique_name("a.pdf", used) == "a (3).pdf"
    assert _unique_name("noext", used) == "noext"
    assert _unique_name("noext", used) == "noext (2)"


def _att(media_kind=None, source_type="file") -> Attachment:
    return Attachment(
        external_id="1",
        file_name="f",
        download_url="https://x/f",
        source_type=source_type,
        media_kind=media_kind,
    )


def test_wanted_video_filter():
    video = _att(media_kind="video")
    doc = _att()
    assert _wanted(video, include_videos=True, include_files=False) is True
    assert _wanted(video, include_videos=False, include_files=True) is False
    assert _wanted(doc, include_videos=False, include_files=True) is True
    assert _wanted(doc, include_videos=True, include_files=False) is False
    # default: everything
    assert _wanted(video, include_videos=True, include_files=True) is True
    assert _wanted(doc, include_videos=True, include_files=True) is True
