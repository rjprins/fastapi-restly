# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

import os
import sys
from importlib.metadata import version as _package_version
from pathlib import Path
from xml.etree import ElementTree

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = "FastAPI-Restly"
copyright = "2025, Rutger Prins"
author = "Rutger Prins"
release = _package_version("fastapi-restly")
version = ".".join(release.split(".")[:2])

# Which slot in the published site this build represents: "latest" (the live
# root) or a frozen minor like "0.7". Drives baseurl, the switcher's current
# entry, and whether the build is kept out of search indexes.
DOCS_VERSION = os.environ.get("DOCS_VERSION", "latest")
SITE_URL = "https://www.fastapi-restly.org/"

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

html_baseurl = SITE_URL if DOCS_VERSION == "latest" else f"{SITE_URL}{DOCS_VERSION}/"
sitemap_url_scheme = "{link}"
extensions = [
    "myst_parser",
    "sphinx_sitemap",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx_copybutton",
    "sphinx_design",
    "sphinx_reredirects",
    "sphinx.ext.intersphinx",
]

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "sqlalchemy": ("https://docs.sqlalchemy.org/en/20", None),
    "pydantic": ("https://docs.pydantic.dev/latest", None),
    "fastapi": ("https://fastapi.tiangolo.com", None),
}
intersphinx_timeout = 30

# Old published URLs -> their post-restructure homes.
redirects = {
    "pytest_fixtures": "howto_testing.html",
    "the_handle_design": "customize.html",
    "howto_override_endpoints": "customize.html",
}
myst_heading_anchors = 3
myst_enable_extensions = ["colon_fence", "substitution"]
# Backticks baked in: substitutions don't fire inside literal spans.
myst_substitutions = {"release": f"`{release}`"}

sys.path.insert(0, os.path.abspath(".."))
autosummary_generate = True
autodoc_default_options = {
    "members": True,
    "undoc-members": True,
    "show-inheritance": True,
}

templates_path = ["_templates"]
exclude_patterns = []


# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = "pydata_sphinx_theme"
html_theme_options = {
    "logo": {"text": "FastAPI-Restly"},
    "github_url": "https://github.com/rjprins/fastapi-restly",
    "use_edit_page_button": True,
    "show_toc_level": 2,
    "navigation_depth": 3,
    "icon_links": [],
    # All eight top-level sections in the header; no "More" dropdown.
    "header_links_before_dropdown": 8,
    # Version dropdown. json_url is absolute so frozen snapshots read the same
    # canonical list and surface versions published after they were built.
    "switcher": {
        "json_url": f"{SITE_URL}switcher.json",
        "version_match": DOCS_VERSION,
    },
    "navbar_end": ["version-switcher", "theme-switcher", "navbar-icon-links"],
    # Right sidebar: page TOC, then the newsletter call-to-action. The
    # edit-this-page/show-source page-tools block is intentionally dropped.
    "secondary_sidebar_items": ["page-toc", "newsletter-link"],
}
html_context = {
    "github_user": "rjprins",
    "github_repo": "fastapi-restly",
    "github_version": "main",
    "doc_path": "docs",
}
html_static_path = ["_static"]
# CNAME must ship in every publish: ghp-import replaces the gh-pages branch
# wholesale, and GitHub Pages drops the custom domain if the file disappears.
html_extra_path = ["robots.txt", "CNAME"]
html_logo = "_static/restly-cat.png"
html_css_files = ["custom.css"]
html_js_files = ["newsletter.js"]


def _canonical_index_url(app, pagename):
    if pagename == "index":
        return app.config.html_baseurl.rstrip("/") + "/"
    if pagename.endswith("/index"):
        return f"{app.config.html_baseurl.rstrip('/')}/{pagename[:-6]}/"
    return None


def _canonicalize_index_page(app, pagename, templatename, context, doctree):
    """Use GitHub Pages directory URLs as canonicals for index pages."""
    pageurl = _canonical_index_url(app, pagename)
    if pageurl:
        context["pageurl"] = pageurl


def _noindex_snapshots(app, pagename, templatename, context, doctree):
    """Keep frozen version snapshots out of search indexes; root stays the
    single indexed copy."""
    if DOCS_VERSION != "latest":
        context["metatags"] = (
            context.get("metatags", "") + '<meta name="robots" content="noindex, follow">\n'
        )


def _canonicalize_sitemap_index(app, exception):
    if exception:
        return

    sitemap_path = Path(app.outdir) / app.config.sitemap_filename
    if not sitemap_path.exists():
        return

    sitemap_namespace = "http://www.sitemaps.org/schemas/sitemap/0.9"
    ElementTree.register_namespace("", sitemap_namespace)
    tree = ElementTree.parse(sitemap_path)
    namespace = {"sitemap": sitemap_namespace}

    changed = False
    for loc in tree.findall(".//sitemap:loc", namespace):
        if loc.text and loc.text.endswith("/index.html"):
            loc.text = loc.text[: -len("index.html")]
            changed = True

    if changed:
        tree.write(sitemap_path, xml_declaration=True, encoding="utf-8")


def setup(app):
    app.connect("html-page-context", _canonicalize_index_page)
    app.connect("html-page-context", _noindex_snapshots)
    app.connect("build-finished", _canonicalize_sitemap_index, priority=900)
