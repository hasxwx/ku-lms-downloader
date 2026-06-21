"""Command-line interface: paste an LMS link, download its attachments and videos."""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys

from .client import LmsClient
from .config import Config
from .downloader import DownloadStats, download_materials
from .models import Material
from .urls import DEFAULT_BASE_URL, parse_lms_url

logger = logging.getLogger("kulms")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kulms",
        description="고려대 LMS 링크의 첨부파일과 주차학습 영상을 다운로드합니다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "예시:\n"
            "  kulms https://mylms.korea.ac.kr/courses/12345\n"
            "  kulms https://mylms.korea.ac.kr/courses/12345/modules --videos-only\n"
            "  kulms 12345 --all -o ./내려받기\n"
        ),
    )
    parser.add_argument("url", help="LMS 링크 또는 course id (예: .../courses/12345 또는 12345)")
    parser.add_argument(
        "-o", "--out", default="downloads", type=Path, help="출력 폴더 (기본: ./downloads)"
    )

    scope = parser.add_argument_group("범위 (지정하지 않으면 링크에서 자동 판단)")
    scope.add_argument("--modules", action="store_true", help="주차학습/강의자료(모듈) 첨부")
    scope.add_argument("--assignments", action="store_true", help="과제 첨부")
    scope.add_argument(
        "--board", "--include-board", dest="board", action="store_true", help="게시판(공지/자료실) 첨부"
    )
    scope.add_argument("--all", action="store_true", help="모듈 + 과제 + 게시판 전부")

    media = parser.add_mutually_exclusive_group()
    media.add_argument("--videos-only", action="store_true", help="영상/미디어만 받기")
    media.add_argument("--files-only", action="store_true", help="영상 제외, 문서/파일만 받기")

    parser.add_argument("--save-links", action="store_true", help="외부 링크를 .url.txt 파일로 저장")
    parser.add_argument("--overwrite", action="store_true", help="이미 받은 파일도 다시 받기")
    parser.add_argument("--list", action="store_true", help="다운로드 없이 목록만 출력")
    parser.add_argument("--base-url", default="", help="LMS base URL 강제 지정 (기본: 링크에서 추론)")
    parser.add_argument("-v", "--verbose", action="store_true", help="자세한 로그")
    return parser


def _resolve_scopes(args: argparse.Namespace, url_scope: str) -> tuple[bool, bool, bool]:
    """Return (want_modules, want_assignments, want_board)."""
    if args.all:
        return True, True, True
    if args.modules or args.assignments or args.board:
        return args.modules, args.assignments, args.board
    # No explicit scope flags → derive from the pasted URL.
    if url_scope == "modules":
        return True, False, False
    if url_scope == "assignments":
        return False, True, False
    if url_scope == "board":
        return False, False, True
    # "all" (a bare course link): modules + assignments by default; board is opt-in.
    return True, True, False


def _collect_materials(
    client: LmsClient,
    target,
    *,
    want_modules: bool,
    want_assignments: bool,
    want_board: bool,
) -> list[Material]:
    materials: list[Material] = []
    if want_modules:
        logger.info("주차학습/강의자료(모듈) 조회 중...")
        materials.extend(client.fetch_module_materials(target.course_id))
    if want_assignments:
        logger.info("과제 조회 중...")
        materials.extend(client.fetch_assignment_materials(target.course_id, target.assignment_id))
    if want_board:
        logger.info("게시판 조회 중...")
        materials.extend(client.fetch_board_materials(target.course_id))
    return materials


def _print_listing(materials: list[Material]) -> None:
    total = 0
    for material in materials:
        print(f"\n[{material.kind}] {material.title}")
        for attachment in material.attachments:
            total += 1
            tag = attachment.media_kind or attachment.source_type
            print(f"    - ({tag}) {attachment.file_name}")
    print(f"\n총 {len(materials)}개 항목, 첨부 {total}개")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )

    config = Config.load()
    default_base = args.base_url or config.base_url or DEFAULT_BASE_URL

    try:
        target = parse_lms_url(args.url, default_base_url=default_base)
    except ValueError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2

    # The pasted link's host wins over the env default; --base-url overrides everything.
    base_url = args.base_url or target.base_url
    config.require_credentials()

    client = LmsClient(
        base_url=base_url,
        username=config.username,
        password=config.password,
        login_user_id=config.login_user_id,
    )

    want_modules, want_assignments, want_board = _resolve_scopes(args, target.scope)
    logger.info(
        "course %s @ %s — 모듈:%s 과제:%s 게시판:%s",
        target.course_id,
        base_url,
        want_modules,
        want_assignments,
        want_board,
    )

    try:
        materials = _collect_materials(
            client,
            target,
            want_modules=want_modules,
            want_assignments=want_assignments,
            want_board=want_board,
        )
    except RuntimeError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1

    if not materials:
        print("다운로드할 첨부를 찾지 못했습니다.", file=sys.stderr)
        return 0

    if args.list:
        _print_listing(materials)
        return 0

    include_videos = not args.files_only
    include_files = not args.videos_only

    stats: DownloadStats = download_materials(
        client,
        materials,
        args.out,
        include_videos=include_videos,
        include_files=include_files,
        save_links=args.save_links,
        overwrite=args.overwrite,
    )

    print(
        f"\n완료 — 받음 {stats.downloaded} · 건너뜀 {stats.skipped} · "
        f"링크 {stats.links} · 실패 {stats.failed}  →  {args.out}"
    )
    return 1 if stats.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
