"""Offline tests for the data-wipe guard in test_rules_postgres.py (no Postgres needed)."""
import importlib.util
from pathlib import Path

import pytest

from app.config import Settings
from tests import test_rules_postgres as guard_mod

PW = "s3cretMARKER"
CHECK = guard_mod.check_test_database_url
Refused = guard_mod.UnsafeTestDatabase


def _url(name="ticks_test", host="localhost", port=":5432", pw=PW):
    return f"postgresql://postgres:{pw}@{host}{port}/{name}"


def _refusal(test_url, database_url=""):
    with pytest.raises(Refused) as exc:
        CHECK(test_url, database_url)
    msg = str(exc.value)
    assert PW not in msg
    assert test_url not in msg
    assert database_url == "" or database_url not in msg
    return msg


# Rule 1

def test_rule1_name_ending_in_test_passes():
    CHECK(_url("ticks_test"), "")


@pytest.mark.parametrize("name", ["ticks", "test", "ticks_tes", "ticks_test_old"])
def test_rule1_other_names_refused(name):
    assert "Rule 1" in _refusal(_url(name))


@pytest.mark.parametrize("url", [_url(""), "postgresql://u:x@localhost:5432", "postgresql://u:x@localhost:5432/"])
def test_rule1_empty_name_refused(url):
    assert "Rule 1" in _refusal(url)


def test_rule1_name_exactly_underscore_test_allowed():
    CHECK(_url("_test"), "")


def test_rule1_case_ignored():
    CHECK(_url("ticks_TEST"), "")
    CHECK(_url("_TEST"), "")


def test_rule1_percent_encoded_name_decoded_first():
    CHECK(_url("ticks%5Ftest"), "")
    assert "Rule 1" in _refusal(_url("ticks%5Fprod"))


def test_rule1_query_string_not_part_of_name():
    CHECK(_url("ticks_test") + "?sslmode=disable", "")
    assert "Rule 1" in _refusal(_url("ticks") + "?x=_test")


# Rule 2

def test_rule2_same_host_port_name_refused_password_ignored():
    msg = _refusal(_url("ticks_test", pw=PW), _url("ticks_test", pw="other"))
    assert "Rule 2" in msg


def test_rule2_differs_in_one_part_passes():
    base = _url("ticks_test")
    CHECK(base, _url("ticks_test", host="db.example"))
    CHECK(base, _url("ticks_test", port=":5433"))
    CHECK(base, _url("other_test"))


def test_rule2_hosts_compared_as_written():
    CHECK(_url(host="localhost"), _url(host="127.0.0.1"))


def test_rule2_missing_port_is_5432():
    assert "Rule 2" in _refusal(_url(port=""), _url(port=":5432"))
    assert "Rule 2" in _refusal(_url(port=":5432"), _url(port=""))
    CHECK(_url(port=""), _url(port=":5433"))


def test_rule2_percent_encoded_name_compared_decoded():
    assert "Rule 2" in _refusal(_url("ticks%5Ftest"), _url("ticks_test"))


@pytest.mark.parametrize("database_url", ["", "   "])
def test_rule2_passes_when_database_url_empty(database_url):
    CHECK(_url(), database_url)


# Invalid input

def test_invalid_port_refused_loudly():
    assert _refusal("postgresql://u:x@localhost:abc/ticks_test")
    assert _refusal(_url(), "postgresql://u:x@localhost:abc/ticks")


def test_unparseable_url_refused_loudly():
    assert _refusal("postgresql://u:x@[::1/ticks_test")
    assert _refusal(_url(), "postgresql://u:x@[::1/ticks")


# DATABASE_URL is read through app.config.Settings

def test_database_url_read_from_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:x@h:1/e")
    assert guard_mod.app_database_url() == "postgresql://u:x@h:1/e"


def test_database_url_read_from_env_file(monkeypatch, tmp_path):
    env_file = Path(tmp_path) / ".env"
    env_file.write_text("DATABASE_URL=postgresql://u:x@fromfile:1/e\n", encoding="utf-8")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", (env_file,))
    assert guard_mod.app_database_url() == "postgresql://u:x@fromfile:1/e"
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:x@fromenv:1/e")
    assert guard_mod.app_database_url() == "postgresql://u:x@fromenv:1/e"


# Module behaviour on import (collection)

def _load_copy():
    path = Path(guard_mod.__file__)
    spec = importlib.util.spec_from_file_location("tests._guard_copy", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_import_unset_does_not_fail(monkeypatch):
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    mod = _load_copy()
    assert mod.pytestmark.mark.args[0] is True  # skipif condition: URL unset


def test_import_refuses_before_any_connection(monkeypatch):
    # Unreachable host: a connection attempt would not give a Rule message.
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://u:x@192.0.2.1:5432/ticks")
    with pytest.raises(Exception, match="Rule 1"):
        _load_copy()


def test_import_refusal_for_rule2(monkeypatch):
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://u:x@192.0.2.1:5432/ticks_test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://v:y@192.0.2.1/ticks_test")
    with pytest.raises(Exception, match="Rule 2"):
        _load_copy()
