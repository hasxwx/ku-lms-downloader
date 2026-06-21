from __future__ import annotations

import os

from kulms.config import _load_dotenv


def _write_env(tmp_path, text: str):
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    return path


def test_plain_value(tmp_path, monkeypatch):
    monkeypatch.delenv("KULMS_T_PLAIN", raising=False)
    _load_dotenv(_write_env(tmp_path, "KULMS_T_PLAIN=plain\n"))
    assert os.environ["KULMS_T_PLAIN"] == "plain"


def test_double_quotes_stripped_once(tmp_path, monkeypatch):
    monkeypatch.delenv("KULMS_T_DQ", raising=False)
    _load_dotenv(_write_env(tmp_path, 'KULMS_T_DQ="secret"\n'))
    assert os.environ["KULMS_T_DQ"] == "secret"


def test_single_quotes_stripped_once(tmp_path, monkeypatch):
    monkeypatch.delenv("KULMS_T_SQ", raising=False)
    _load_dotenv(_write_env(tmp_path, "KULMS_T_SQ='secret'\n"))
    assert os.environ["KULMS_T_SQ"] == "secret"


def test_inner_quotes_preserved(tmp_path, monkeypatch):
    # Only the outer matching pair is removed; inner quotes are part of the value.
    monkeypatch.delenv("KULMS_T_INNER", raising=False)
    _load_dotenv(_write_env(tmp_path, "KULMS_T_INNER=\"'mixed'\"\n"))
    assert os.environ["KULMS_T_INNER"] == "'mixed'"


def test_unbalanced_quote_not_stripped(tmp_path, monkeypatch):
    # A password containing (but not wrapped in) a quote must survive intact.
    monkeypatch.delenv("KULMS_T_UNBAL", raising=False)
    _load_dotenv(_write_env(tmp_path, "KULMS_T_UNBAL=pa'ss\n"))
    assert os.environ["KULMS_T_UNBAL"] == "pa'ss"


def test_existing_env_var_wins_even_when_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("KULMS_T_EXISTING", "")
    _load_dotenv(_write_env(tmp_path, "KULMS_T_EXISTING=fromfile\n"))
    assert os.environ["KULMS_T_EXISTING"] == ""
