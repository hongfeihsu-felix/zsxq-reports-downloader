#!/usr/bin/env python3
r"""Report server for Windows — serves static files + accepts HTTP uploads.

Usage:
    python windows_serve.py 8080 C:\\Users\\Administrator\\reports

GET  /report.html    → serve the file
POST /upload         → receive file upload (form field: "file")
"""
import http.server
import os
import sys
import cgi
from pathlib import Path

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
ROOT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(r"C:\Users\Administrator\reports")
ROOT.mkdir(parents=True, exist_ok=True)


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"  {self.client_address[0]} - {args[0]}")

    def do_POST(self):
        if self.path == "/upload":
            try:
                form = cgi.FieldStorage(
                    fp=self.rfile,
                    headers=self.headers,
                    environ={"REQUEST_METHOD": "POST",
                             "CONTENT_TYPE": self.headers["Content-Type"]}
                )
                file_item = form["file"]
                filename = file_item.filename or "report.html"
                # Sanitize: strip any path components
                safe_name = Path(filename).name
                save_path = ROOT / safe_name
                with open(save_path, "wb") as f:
                    f.write(file_item.file.read())
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(f"OK {safe_name}".encode())
                print(f"  ✅ Uploaded: {safe_name}")
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(f"ERROR {e}".encode())
                print(f"  ❌ Upload failed: {e}")
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        # For /list, show directory listing
        if self.path == "/list":
            files = sorted(ROOT.glob("*"))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            html = "<h2>Reports</h2><ul>"
            for f in files:
                url = f"/{f.name}"
                html += f'<li><a href="{url}">{f.name}</a></li>'
            html += "</ul>"
            self.wfile.write(html.encode())
            return
        super().do_GET()


os.chdir(str(ROOT))
print(f"Serving {ROOT} on port {PORT}")
print(f"Upload: POST http://<ip>:{PORT}/upload")
http.server.HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
