import os

import pytest

from utils import build_cookie_header, deep_inject_env, normalize_auth_headers, parse_cookie_header


def test_deep_inject_env_replaces_placeholders(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOKEN", "abc123")
    data = {
        "headers": {"auth": "${TOKEN}"},
        "items": ["${TOKEN}", {"nested": "${TOKEN}"}],
        "tuple": ("${TOKEN}", 1),
    }
    injected = deep_inject_env(data)
    assert injected["headers"]["auth"] == "abc123"
    assert injected["items"][0] == "abc123"
    assert injected["items"][1]["nested"] == "abc123"
    assert injected["tuple"][0] == "abc123"
    assert injected["tuple"][1] == 1


def test_parse_cookie_header_handles_pairs() -> None:
    parsed = parse_cookie_header("A=1; B=2; C=hello")
    assert parsed == {"A": "1", "B": "2", "C": "hello"}


def test_normalize_auth_headers_prefers_cookie_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTH_TOKEN", raising=False)
    headers = {"cookie": "AUTH_TOKEN=cookie-token; OTHER=1"}
    token = normalize_auth_headers(headers)
    assert token == "cookie-token"
    assert headers["auth_token"] == "cookie-token"


def test_normalize_auth_headers_uses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_TOKEN", "env-token")
    headers = {"cookie": "OTHER=1"}
    token = normalize_auth_headers(headers)
    assert token == "env-token"
    assert headers["auth_token"] == "env-token"


def test_build_cookie_header_orders_preferred_keys() -> None:
    header = build_cookie_header(
        cookie_pairs={"B": "2", "A": "1", "C": "3"},
        preferred_keys=("A", "C"),
    )
    assert header == "A=1; C=3; B=2"
