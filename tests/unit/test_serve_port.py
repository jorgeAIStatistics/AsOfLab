"""`asoflab serve` opens its port first and only then says where it is serving."""

import errno
import socket
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from asoflab.api import listen
from asoflab.api.listen import (
    HIGHEST_PORT,
    SUGGESTION_RANGE,
    PortUnavailable,
    local_url,
    open_listener,
)
from asoflab.cli import app

HOST = "127.0.0.1"


@pytest.fixture
def busy_port() -> Iterator[int]:
    """A port that another listening socket holds for the length of the test."""
    while True:
        holder = socket.create_server((HOST, 0))
        port = holder.getsockname()[1]
        # Leave room above it, so that a free port to suggest can exist.
        if port <= HIGHEST_PORT - SUGGESTION_RANGE:
            break
        holder.close()
    with holder:
        yield port


def serve(directory: Path, port: int) -> list[str]:
    """Arguments for `asoflab serve` that read nothing outside the test's own directory."""
    return [
        "serve",
        "--runs",
        str(directory),
        "--results",
        str(directory / "results"),
        "--web",
        str(directory / "web"),
        "--port",
        str(port),
    ]


def test_a_free_port_is_opened_and_accepts_connections() -> None:
    with open_listener(HOST, 0) as listener:
        port = listener.getsockname()[1]
        assert port > 0
        with socket.create_connection((HOST, port), timeout=5):
            pass


def test_a_port_held_by_another_listener_is_refused(busy_port: int) -> None:
    with pytest.raises(PortUnavailable) as raised:
        open_listener(HOST, busy_port)
    assert raised.value.in_use
    assert raised.value.port == busy_port


def test_the_suggested_port_can_actually_be_opened(busy_port: int) -> None:
    with pytest.raises(PortUnavailable) as raised:
        open_listener(HOST, busy_port)
    suggestion = raised.value.free_port
    assert suggestion is not None
    assert busy_port < suggestion <= busy_port + SUGGESTION_RANGE
    with open_listener(HOST, suggestion):
        pass


def test_another_kind_of_failure_keeps_the_systems_reason_and_suggests_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempted = []

    def refuse(host: str, port: int) -> socket.socket:
        attempted.append(port)
        raise PermissionError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(listen, "_bind", refuse)
    with pytest.raises(PortUnavailable) as raised:
        open_listener(HOST, 80)
    assert not raised.value.in_use
    assert raised.value.reason == "Permission denied"
    assert raised.value.free_port is None
    # A suggestion is only worth looking for when the port is busy.
    assert attempted == [80]


def test_an_ipv6_literal_is_bracketed_in_the_printed_address() -> None:
    assert local_url("127.0.0.1", 8000) == "http://127.0.0.1:8000"
    assert local_url("::1", 8000) == "http://[::1]:8000"


def test_serve_announces_no_address_when_the_port_is_taken(tmp_path: Path, busy_port: int) -> None:
    result = CliRunner().invoke(app, serve(tmp_path, busy_port))
    assert result.exit_code == 2
    assert "Serving runs" not in result.output
    assert f"Port {busy_port} on {HOST} is already in use" in result.output
    assert "--port" in result.output


def test_a_port_number_outside_the_valid_range_is_a_usage_error(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, serve(tmp_path, 70000))
    assert result.exit_code == 2
    assert "Serving runs" not in result.output
