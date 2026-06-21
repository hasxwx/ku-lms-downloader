from __future__ import annotations

from kulms.downloader import _ensure_extension, _safe_name, _unique_name, _wanted
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


def _att(media_kind=None, source_type="file", file_name="f", download_url="https://x/f") -> Attachment:
    return Attachment(
        external_id="1",
        file_name=file_name,
        download_url=download_url,
        source_type=source_type,
        media_kind=media_kind,
    )


def test_ensure_extension_video_without_suffix():
    a = _att(media_kind="video", file_name="3장리뷰_6장", download_url="https://cdn/x/clip.mp4")
    assert _ensure_extension("3장리뷰_6장", a) == "3장리뷰_6장.mp4"


def test_ensure_extension_video_with_bogus_internal_dot():
    # "Ch. 6(Review)_Ch. 7" -> Path.suffix is a bogus ' 7', not a real video ext.
    a = _att(media_kind="video", file_name="Ch. 6(Review)_Ch. 7", download_url="https://cdn/x/v")
    assert _ensure_extension("Ch. 6(Review)_Ch. 7", a) == "Ch. 6(Review)_Ch. 7.mp4"


def test_ensure_extension_video_already_has_real_ext():
    a = _att(media_kind="video", file_name="lecture.mkv", download_url="https://cdn/x/lecture.mkv")
    assert _ensure_extension("lecture.mkv", a) == "lecture.mkv"


def test_ensure_extension_video_uses_url_ext_when_known():
    a = _att(media_kind="video", file_name="rec", download_url="https://cdn/x/rec.webm?token=1")
    assert _ensure_extension("rec", a) == "rec.webm"


def test_ensure_extension_audio_default():
    a = _att(media_kind="audio", file_name="podcast", download_url="https://cdn/x/p")
    assert _ensure_extension("podcast", a) == "podcast.m4a"


def test_ensure_extension_non_media_pdf_unchanged():
    a = _att(file_name="EngMath_CH8.pdf", download_url="https://x/EngMath_CH8.pdf")
    assert _ensure_extension("EngMath_CH8.pdf", a) == "EngMath_CH8.pdf"


def test_ensure_extension_non_media_no_suffix_uses_url():
    a = _att(file_name="syllabus", download_url="https://x/files/9/syllabus.pdf")
    assert _ensure_extension("syllabus", a) == "syllabus.pdf"


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
