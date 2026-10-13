"""Start `asoflab serve` as a real process and hold it to the address it prints.

The command opens its port itself and hands the open socket to the web server.
Only a real process shows that the hand-over works: that the printed address is
the one that answers, and that a second copy asked for the same port starts
nothing and says so.
"""

import http.client
import json
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from typer.testing import CliRunner

from asoflab.cli import app

pytestmark = pytest.mark.slow

ANNOUNCEMENT = re.compile(r"at http://127\.0\.0\.1:(\d+)\s*$")
# Generous, so that a slow continuous-integration runner cannot make this fail.
STARTUP_SECONDS = 120


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


def test_serve_answers_at_the_address_it_prints_and_a_second_copy_is_refused(
    tmp_path: Path,
) -> None:
    # Port 0: the operating system picks a free port, so the test cannot collide with
    # anything else on the machine, and the command has to report the port it was given.
    with subprocess.Popen(
        [sys.executable, "-m", "asoflab.cli", *serve(tmp_path, 0)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        assert process.stdout is not None
        assert process.stderr is not None
        # Reading the first line blocks, so a process that never prints is killed instead.
        watchdog = threading.Timer(STARTUP_SECONDS, process.kill)
        watchdog.start()
        try:
            first_line = process.stdout.readline()
            announced = ANNOUNCEMENT.search(first_line)
            if announced is None:
                process.kill()
                pytest.fail(
                    f"No address was announced. stdout: {first_line!r}, "
                    f"stderr: {process.stderr.read()!r}"
                )
            port = int(announced.group(1))
            assert port != 0

            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=STARTUP_SECONDS)
            try:
                connection.request("GET", "/api/health")
                response = connection.getresponse()
                assert response.status == 200
                assert json.loads(response.read())["status"] == "ok"
            finally:
                connection.close()

            second = CliRunner().invoke(app, serve(tmp_path, port))
            assert second.exit_code == 2
            assert "Serving runs" not in second.output
            assert f"Port {port} on 127.0.0.1 is already in use" in second.output
        finally:
            watchdog.cancel()
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
