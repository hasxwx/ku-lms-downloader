from __future__ import annotations

import pytest

from kulms.urls import parse_lms_url


def test_bare_course_home_is_scope_all():
    target = parse_lms_url("https://lms.korea.ac.kr/courses/12345")
    assert target.course_id == "12345"
    assert target.base_url == "https://lms.korea.ac.kr"
    assert target.scope == "all"
    assert target.assignment_id is None


def test_modules_url():
    target = parse_lms_url("https://lms.korea.ac.kr/courses/777/modules")
    assert target.course_id == "777"
    assert target.scope == "modules"


def test_modules_with_fragment():
    target = parse_lms_url("https://lms.korea.ac.kr/courses/777/modules#42")
    assert target.course_id == "777"
    assert target.scope == "modules"


def test_assignment_detail_url_captures_id():
    target = parse_lms_url("https://lms.korea.ac.kr/courses/9/assignments/678")
    assert target.course_id == "9"
    assert target.scope == "assignments"
    assert target.assignment_id == "678"


def test_external_tools_url_is_modules_scope():
    target = parse_lms_url("https://lms.korea.ac.kr/courses/9/external_tools/5")
    assert target.scope == "modules"


def test_missing_scheme_is_assumed_https():
    target = parse_lms_url("lms.korea.ac.kr/courses/55")
    assert target.base_url == "https://lms.korea.ac.kr"
    assert target.course_id == "55"


def test_bare_numeric_course_id_uses_default_base():
    target = parse_lms_url("12345", default_base_url="https://lms.example.ac.kr")
    assert target.course_id == "12345"
    assert target.base_url == "https://lms.example.ac.kr"
    assert target.scope == "all"


def test_base_url_is_taken_from_link_host():
    target = parse_lms_url("https://lms.other.ac.kr/courses/1/modules")
    assert target.base_url == "https://lms.other.ac.kr"


def test_empty_raises():
    with pytest.raises(ValueError):
        parse_lms_url("   ")


def test_non_course_url_raises():
    with pytest.raises(ValueError):
        parse_lms_url("https://lms.korea.ac.kr/profile/settings")
