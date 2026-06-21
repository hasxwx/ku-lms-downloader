from __future__ import annotations

import argparse

from kulms.cli import _resolve_scopes, build_parser


def _ns(**kw) -> argparse.Namespace:
    base = dict(modules=False, assignments=False, board=False, all=False)
    base.update(kw)
    return argparse.Namespace(**base)


def test_resolve_scopes_all_flag():
    assert _resolve_scopes(_ns(all=True), "modules") == (True, True, True)


def test_resolve_scopes_explicit_flags_win():
    assert _resolve_scopes(_ns(board=True), "all") == (False, False, True)
    assert _resolve_scopes(_ns(modules=True, assignments=True), "board") == (True, True, False)


def test_resolve_scopes_from_url_when_no_flags():
    assert _resolve_scopes(_ns(), "modules") == (True, False, False)
    assert _resolve_scopes(_ns(), "assignments") == (False, True, False)
    # bare course link → modules + assignments, board opt-in
    assert _resolve_scopes(_ns(), "all") == (True, True, False)


def test_parser_accepts_core_invocation():
    args = build_parser().parse_args(["https://lms.korea.ac.kr/courses/1", "--videos-only", "-o", "out"])
    assert args.url.endswith("/courses/1")
    assert args.videos_only is True
    assert str(args.out) == "out"


def test_board_alias_include_board():
    args = build_parser().parse_args(["12345", "--include-board"])
    assert args.board is True
