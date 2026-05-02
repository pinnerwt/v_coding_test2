import contextlib
import http.server
import socketserver
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent / "fixtures" / "sites"


class _Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)


@pytest.fixture
def http_fixture_server():
    with contextlib.ExitStack() as stack:

        def start(rel_path):
            httpd = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
            port = httpd.server_address[1]
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            stack.callback(httpd.shutdown)
            return f"http://127.0.0.1:{port}/{rel_path}"

        yield start
