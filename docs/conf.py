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

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

html_baseurl = "https://rjprins.github.io/fastapi-restly/"
sitemap_url_scheme = "{link}"
extensions = [
    "myst_parser",
    "sphinx_sitemap",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx_copybutton",
    "sphinx_design",
]
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
    "github_url": "https://github.com/rjprins/fastapi-restly",
    "use_edit_page_button": True,
    "show_toc_level": 2,
    "navigation_depth": 3,
    "icon_links": [],
}
html_context = {
    "github_user": "rjprins",
    "github_repo": "fastapi-restly",
    "github_version": "main",
    "doc_path": "docs",
}
html_static_path = ["_static"]
html_extra_path = ["robots.txt"]
html_logo = "_static/restly-cat.png"
html_css_files = ["custom.css"]


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
    app.connect("build-finished", _canonicalize_sitemap_index, priority=900)
