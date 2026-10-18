"""Print the singlehtml docs build to a phone-sized PDF with headless Chromium."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

BROWSERS = ("chromium", "chromium-browser", "google-chrome-stable", "google-chrome")
CSS_NAME = "phone-pdf.css"

# Pages about the size of a phone screen, so a page fits the screen width at a
# readable size. The site's scroll containers would clip each page, so the CSS
# makes them visible and makes wide code and tables wrap to the page width.
PHONE_CSS = """\
@page { size: 110mm 220mm; margin: 6mm 5mm; }
html { font-size: 14px !important; }
.bd-header, .bd-sidebar-primary, .bd-sidebar-secondary, .bd-footer, .bd-footer-article,
.bd-footer-content, .prev-next-area, .header-article, .bd-header-announcement,
.copybtn, .headerlink, .theme-switch-button, .search-button, .skip-link,
#pst-back-to-top { display: none !important; }
.bd-container, .bd-container__inner, .bd-main, .bd-content, .bd-article-container,
.bd-page-width {
  max-width: none !important; width: 100% !important;
  margin: 0 !important; padding: 0 !important;
}
.bd-article { padding: 0 !important; }
html, body, .bd-page-width, .bd-container, .bd-container__inner, .bd-main, .bd-content,
.bd-article-container, .bd-article, article, section, .pst-scrollable-table-container {
  overflow: visible !important; height: auto !important; min-height: 0 !important;
}
html, body { scrollbar-width: none !important; scrollbar-gutter: auto !important; }
pre, pre code {
  white-space: pre-wrap !important; word-break: break-word !important;
  overflow: visible !important; font-size: 0.8rem !important;
}
span.pre, code, .sig {
  white-space: pre-wrap !important; overflow-wrap: anywhere !important;
  word-break: break-word !important;
}
p, li, dd, dt, td, th, a { overflow-wrap: anywhere; }
h1, h2, h3, h4 { break-after: avoid; }
table {
  table-layout: fixed !important; width: 100% !important; max-width: 100% !important;
  min-width: 0 !important; font-size: 0.8rem;
}
img { max-width: 100% !important; height: auto !important; }
"""


def find_browser() -> str | None:
    if os.environ.get("CHROME"):
        return os.environ["CHROME"]
    return next((path for name in BROWSERS if (path := shutil.which(name))), None)


def add_phone_css(html_dir: Path) -> Path:
    """Write the phone CSS next to the build and link it from index.html."""
    (html_dir / "_static" / CSS_NAME).write_text(PHONE_CSS)
    index = html_dir / "index.html"
    html = index.read_text()
    link = f'<link rel="stylesheet" href="_static/{CSS_NAME}">'
    if link not in html:
        index.write_text(html.replace("</head>", f"{link}\n</head>", 1))
    return index


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("html_dir", type=Path, help="singlehtml build directory")
    parser.add_argument("output", type=Path, help="path to write the PDF")
    args = parser.parse_args(argv)

    browser = find_browser()
    if browser is None:
        print(
            "Chromium or Google Chrome not found; set CHROME to its path.",
            file=sys.stderr,
        )
        return 1

    index = add_phone_css(args.html_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.unlink(missing_ok=True)
    result = subprocess.run(
        [
            browser,
            "--headless",
            "--disable-gpu",
            "--hide-scrollbars",
            "--no-pdf-header-footer",
            "--generate-pdf-document-outline",
            "--virtual-time-budget=10000",
            f"--print-to-pdf={args.output.resolve()}",
            index.resolve().as_uri(),
        ],
        capture_output=True,
        text=True,
    )
    # Chromium logs harmless noise on stderr, so show it only on failure.
    if result.returncode != 0 or not args.output.exists():
        print(result.stderr, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
