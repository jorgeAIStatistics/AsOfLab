"""Open the listening socket for `asoflab serve`.

`asoflab serve` prints the address it serves at, and that line has to be true.
So the socket is opened here first, the address is printed only once this
process holds it, and the open socket is handed to the web server, which binds
nothing itself. Printing first and binding afterwards can name an address that
belongs to another program.

The socket is set up the way uvicorn sets up its own: IPv6 when the host is an
IPv6 literal, and SO_REUSEADDR so that a restart does not have to wait for the
previous run's connections to expire. Whether a port is available therefore has
the same answer it would have had if the web server had bound it.
"""

import errno
import socket

# How many ports above a busy one are tried when looking for one to suggest.
SUGGESTION_RANGE = 20
HIGHEST_PORT = 65535


class PortUnavailable(Exception):
    """The address could not be opened, and this process is listening on nothing."""

    def __init__(
        self, host: str, port: int, reason: str, *, in_use: bool, free_port: int | None
    ) -> None:
        super().__init__(f"Cannot listen on {host}, port {port}: {reason}")
        self.host = host
        self.port = port
        # The operating system's own description of the failure.
        self.reason = reason
        # True when another program already holds the port.
        self.in_use = in_use
        # A nearby port that could be opened at the moment of the check, if one was found.
        self.free_port = free_port


def _bind(host: str, port: int) -> socket.socket:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    listener = socket.socket(family, socket.SOCK_STREAM)
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host, port))
        # Listening, not only bound: on Linux two sockets that both set SO_REUSEADDR
        # can be bound to one port as long as neither listens.
        listener.listen()
    except OSError:
        listener.close()
        raise
    return listener


def _free_port_above(host: str, port: int) -> int | None:
    for candidate in range(port + 1, min(port + SUGGESTION_RANGE, HIGHEST_PORT) + 1):
        try:
            _bind(host, candidate).close()
        except OSError:
            continue
        return candidate
    return None


def open_listener(host: str, port: int) -> socket.socket:
    """Return a socket that is bound to host and port and already listening.

    Port 0 asks the operating system for any free port; `getsockname()` on the
    result says which one it chose. Raises PortUnavailable when the address
    cannot be opened.
    """
    try:
        return _bind(host, port)
    except OSError as error:
        in_use = error.errno == errno.EADDRINUSE
        raise PortUnavailable(
            host,
            port,
            error.strerror or str(error),
            in_use=in_use,
            free_port=_free_port_above(host, port) if in_use else None,
        ) from error


def local_url(host: str, port: int) -> str:
    """The address to print and to open in a browser."""
    return f"http://[{host}]:{port}" if ":" in host else f"http://{host}:{port}"
