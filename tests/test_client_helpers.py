from __future__ import annotations

from kulms.client import LmsClient


def _client() -> LmsClient:
    # These helpers never touch the network or trigger login.
    return LmsClient(base_url="https://lms.korea.ac.kr", username="u", password="p")


def test_guess_media_kind_by_extension():
    assert LmsClient._guess_media_kind("lecture.mp4") == "video"
    assert LmsClient._guess_media_kind("podcast.mp3") == "audio"
    assert LmsClient._guess_media_kind("slides.pdf") is None


def test_guess_media_kind_by_content_type():
    assert LmsClient._guess_media_kind("noext", "movie") == "video"
    assert LmsClient._guess_media_kind("noext", "video/mp4") == "video"
    assert LmsClient._guess_media_kind("noext", "sound") == "audio"


def test_build_upf_media_url_substitutes_media_file():
    xml = (
        "<root>"
        "<main_media>ch6.mp4</main_media>"
        '<media_uri method="progressive" target="all">https://cdn.kucom.ac.kr/v/[MEDIA_FILE]</media_uri>'
        "</root>"
    )
    assert LmsClient._build_upf_media_url(xml) == "https://cdn.kucom.ac.kr/v/ch6.mp4"


def test_build_upf_media_url_ignores_wrapper_list():
    xml = (
        '<main_media_list count="1"></main_media_list>'
        "<main_media>clip.mp4</main_media>"
        '<media_uri method="progressive" target="mobile">https://cdn/[MEDIA_FILE]</media_uri>'
    )
    assert LmsClient._build_upf_media_url(xml) == "https://cdn/clip.mp4"


def test_build_upf_media_url_returns_none_without_media():
    assert LmsClient._build_upf_media_url("<root></root>") is None


def test_flatten_assignment_groups():
    payload = [
        {"assignments": [{"id": 1}, {"id": 2}]},
        {"assignments": [{"id": 3}]},
        {"no_assignments": True},
    ]
    flat = LmsClient._flatten_assignment_groups(payload)
    assert [a["id"] for a in flat] == [1, 2, 3]


def test_extract_form_finds_action_and_inputs():
    html = (
        '<form action="/learningx/lti/modulebuilder" method="post">'
        '<input name="a" value="1">'
        '<input name="b" value="x y">'
        "</form>"
    )
    form = LmsClient._extract_form(html, "learningx/lti/modulebuilder")
    assert form is not None
    action, inputs = form
    assert action == "/learningx/lti/modulebuilder"
    assert inputs == {"a": "1", "b": "x y"}


def test_extract_form_missing_returns_none():
    assert LmsClient._extract_form("<form action='/other'></form>", "modulebuilder") is None


def test_extract_result_token():
    assert LmsClient._extract_result_token("https://x/cb?result=ABC123&foo=1") == "ABC123"
    assert LmsClient._extract_result_token("https://x/cb?foo=1") is None


def test_extract_board_id_from_html():
    assert LmsClient._extract_board_id_from_html('<div data-board_id="42">') == "42"
    assert LmsClient._extract_board_id_from_html("/learningx/lti/learningx_board/boards/7") == "7"
    assert LmsClient._extract_board_id_from_html("<div></div>") is None


def test_board_post_attachments_resolved_to_absolute_urls():
    client = _client()
    post = {
        "attachments": [
            {"id": "10", "file_name": "공지.pdf", "url": "/files/10/download"},
            {"id": "11", "name": "lecture.mp4", "download_url": "https://cdn/x/lecture.mp4"},
        ]
    }
    atts = client._extract_attachments_from_board_post(post)
    assert len(atts) == 2
    assert atts[0].download_url == "https://lms.korea.ac.kr/files/10/download"
    assert atts[0].file_name == "공지.pdf"
    assert atts[1].media_kind == "video"
    assert atts[1].download_url == "https://cdn/x/lecture.mp4"


def test_extract_attachments_plain_link_item():
    client = _client()
    atts = client._extract_attachments([{"url": "/files/1/download", "title": "Syllabus.pdf"}])
    assert len(atts) == 1
    assert atts[0].source_type == "link"
    assert atts[0].download_url == "https://lms.korea.ac.kr/files/1/download"


def test_resolve_learningx_embed_website_is_link():
    client = _client()
    item = {
        "content_data": {
            "item_content_data": {
                "content_type": "embed",
                "content_subtype": "website",
                "content_id": "abc",
                "weblink": "https://example.com",
                "weblink_title": "외부자료",
            }
        }
    }
    attachment = client._resolve_learningx_content_item(item)
    assert attachment is not None
    assert attachment.source_type == "link"
    assert attachment.download_url == "https://example.com"
    assert attachment.file_name == "외부자료"
