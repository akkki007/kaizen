#!/usr/bin/env python3
"""One-click GitHub App registration (manifest flow).

Serves a page on http://localhost:8765 that posts docs/github-app-manifest.json to GitHub.
After you confirm on GitHub, GitHub redirects back with a one-time `code`, which is written to
the file given as argv[1] (default ./github-app-code.txt). Exchange it with
`POST https://api.github.com/app-manifests/<code>/conversions` (unauthenticated, valid 1 hour).
"""

import html
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

MANIFEST = Path(__file__).resolve().parents[1] / "docs" / "github-app-manifest.json"
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "github-app-code.txt")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:  # keep the code out of any log
        pass

    def _send(self, body: str, status: int = 200) -> None:
        data = body.encode()
        self.send_response(status)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        if url.path == "/callback":
            code = parse_qs(url.query).get("code", [""])[0]
            if not code:
                return self._send("missing code", 400)
            OUT.write_text(code)
            OUT.chmod(0o600)
            return self._send("<h2>GitHub App created.</h2><p>You can close this tab.</p>")
        manifest = html.escape(json.dumps(json.loads(MANIFEST.read_text())), quote=True)
        self._send(
            "<h2>Register the CausalDeploy GitHub App</h2>"
            '<form method="post" action="https://github.com/settings/apps/new?state=cd">'
            f'<input type="hidden" name="manifest" value="{manifest}">'
            '<button type="submit" style="font-size:18px;padding:10px 18px">Continue to GitHub</button>'
            "</form>"
        )


if __name__ == "__main__":
    print("open http://localhost:8765 and confirm on GitHub", flush=True)
    server = HTTPServer(("127.0.0.1", 8765), Handler)
    while not OUT.exists():
        server.handle_request()
    print("code received")
