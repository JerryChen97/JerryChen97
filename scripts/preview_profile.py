#!/usr/bin/env python3
"""Build a self-contained, offline-browsable preview of the profile README."""
from __future__ import annotations

import argparse
import html
from pathlib import Path
import re
import shutil
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
START = "<!-- PULSE:START -->"
END = "<!-- PULSE:END -->"
ASSET_RE = re.compile(r'(?:src|srcset)\s*=\s*["\']([^"\']+)["\']')
MARKDOWN_LINK_RE = re.compile(r'!?\[[^\]]*\]\(([^)]+)\)')
HREF_RE = re.compile(r'href\s*=\s*["\']([^"\']+)["\']')


def check_readme(text: str) -> None:
    if text.count(START) != 1 or text.count(END) != 1 or text.index(START) >= text.index(END):
        raise ValueError("README must contain one correctly ordered Pulse region")
    for target in ASSET_RE.findall(text) + MARKDOWN_LINK_RE.findall(text) + HREF_RE.findall(text):
        # External URLs and mailto: addresses cannot be validated offline.
        if target.startswith(("http://", "https://", "mailto:", "#", "data:")):
            continue
        path = target.split("#", 1)[0].split("?", 1)[0]
        if not path:
            continue
        resolved = (ROOT / path).resolve()
        if not resolved.is_relative_to(ROOT) or not resolved.is_file():
            raise ValueError(f"Broken or unsafe local link: {target}")
    for asset in (ROOT / "assets").glob("*.svg"):
        ET.parse(asset)


def render(text: str) -> str:
    try:
        import markdown
    except ImportError as exc:
        raise RuntimeError("Install requirements-preview.txt to render HTML") from exc
    body = markdown.markdown(text, extensions=["extra", "tables", "sane_lists"])
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>GitHub Profile Preview</title>
<style>
:root { color-scheme: light dark; }
body { max-width: 960px; margin: 0 auto; padding: 24px 20px 72px;
       font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       color: light-dark(#1f2328, #e6edf3); background: light-dark(#fff, #0d1117); }
a { color: light-dark(#0969da, #58a6ff); }
h1,h2 { border-bottom: 1px solid light-dark(#d8dee4, #30363d); padding-bottom: .3em; }
img { max-width: 100%; height: auto; }
table { border-collapse: collapse; width: 100%; }
td,th { border: 1px solid light-dark(#d8dee4, #30363d); padding: 8px 12px; }
code { background: light-dark(#f6f8fa, #161b22); padding: .15em .3em; border-radius: 4px; }
.notice { padding: 12px 16px; margin-bottom: 24px; border: 1px solid #8b949e;
          border-radius: 6px; font-size: 14px; }
</style></head><body>
<div class="notice"><strong>PR preview — not the live GitHub profile.</strong>
Local HTML approximation; GitHub's Markdown renderer may differ. External links are not checked.
</div>
""" + body + "\n</body></html>\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--output", default="profile-preview")
    args = parser.parse_args()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    check_readme(readme)
    if args.check_only:
        print("Profile local links, SVG files and Pulse markers: OK")
        return
    output = Path(args.output).resolve()
    if output == ROOT or ROOT.is_relative_to(output):
        raise ValueError("Output directory must not contain the repository")
    output.mkdir(parents=True, exist_ok=True)
    (output / "index.html").write_text(render(readme), encoding="utf-8")
    shutil.copytree(ROOT / "assets", output / "assets", dirs_exist_ok=True)
    print(f"Preview: {output / 'index.html'}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, ET.ParseError) as exc:
        print(f"Preview validation failed: {exc}", file=sys.stderr)
        sys.exit(1)
