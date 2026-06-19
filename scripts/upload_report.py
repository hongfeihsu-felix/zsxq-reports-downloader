#!/usr/bin/env python3
"""Upload a report to the Windows report server via HTTP POST.

Usage:
    python3 scripts/upload_report.py path/to/report.html
    python3 scripts/upload_report.py path/to/report.html --host 1.2.3.4
    python3 scripts/upload_report.py --list              # list remote files
"""
import argparse
import sys
import os
import requests
from pathlib import Path

# Defaults — set via env vars or CLI
REMOTE_HOST = os.environ.get("REPORT_HOST", "your-server-ip")
REMOTE_PORT = os.environ.get("REPORT_PORT", "8080")


def upload(local_path: str, remote_name: str = None, host: str = None) -> str:
    """Upload file via HTTP POST. Returns the public URL."""
    host = host or REMOTE_HOST
    local = Path(local_path)

    if not local.exists():
        print(f"❌ File not found: {local_path}")
        sys.exit(1)

    remote_name = remote_name or local.name
    url_upload = f"http://{host}:{REMOTE_PORT}/upload"
    url_view = f"http://{host}:{REMOTE_PORT}/{remote_name}"

    print(f"📤 Uploading {local.name} ({local.stat().st_size / 1024:.1f}KB) → {host}...")
    try:
        with open(local, "rb") as f:
            resp = requests.post(
                url_upload,
                files={"file": (remote_name, f)},
                timeout=30
            )
        if resp.status_code == 200:
            print(f"✅ {url_view}")
            return url_view
        else:
            print(f"❌ Upload failed: HTTP {resp.status_code} — {resp.text}")
            sys.exit(1)
    except requests.exceptions.ConnectionError:
        print(f"❌ Cannot connect to {host}:{REMOTE_PORT} — is the server running?")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Upload error: {e}")
        sys.exit(1)


def list_remote(host: str = None):
    """List files on the report server."""
    host = host or REMOTE_HOST
    try:
        resp = requests.get(f"http://{host}:{REMOTE_PORT}/list", timeout=10)
        print(resp.text if resp.status_code == 200 else f"Error: {resp.status_code}")
    except Exception as e:
        print(f"❌ Cannot connect: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Upload reports to Windows server")
    parser.add_argument("file", nargs="?", help="Local file to upload")
    parser.add_argument("--host", help="Server IP/hostname")
    parser.add_argument("--name", help="Remote filename (default: same as local)")
    parser.add_argument("--list", action="store_true", help="List remote files")
    parser.add_argument("--open", action="store_true", help="Open URL in browser")
    args = parser.parse_args()

    if args.list:
        list_remote(args.host)
    elif args.file:
        url = upload(args.file, remote_name=args.name, host=args.host)
        if args.open:
            import webbrowser
            webbrowser.open(url)
    else:
        parser.print_help()
