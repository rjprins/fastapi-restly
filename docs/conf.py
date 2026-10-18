# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

import os
import re
import sys
from importlib.metadata import version as _package_version
from pathlib import Path
from xml.etree import ElementTree

from sphinx.util import logging as sphinx_logging

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

# A cross-reference role whose target does not exist renders as plain text, so
# -W alone never catches a mistyped one. Nitpicky mode turns those into build
# failures; the ignores below cover targets that have no published page to
# point at, which autodoc emits from signatures rather than from prose.
nitpicky = True
nitpick_ignore_regex = [
    (r"py:.*", r"(.*\.)?_.*"),  # private modules and classes, and their TypeVars
    (r"py:.*", r"httpx2\..*"),  # httpx2 publishes no intersphinx inventory
    # Annotated[X, ...] has no page. On Python 3.10, Annotated also takes the
    # module of X, so autodoc writes datetime.Annotated, which has no page too.
    (r"py:.*", r"[\w.]+\.Annotated(\[.*)?"),
]
nitpick_ignore = [
    # Names autodoc reads off an annotation unqualified, so intersphinx cannot
    # resolve them.
    ("py:class", "ConfigDict"),
    ("py:class", "DeclarativeBase"),
    ("py:class", "FastAPI"),
    ("py:class", "Json"),
    ("py:class", "MetaData"),
    ("py:class", "Path"),
    ("py:class", "fastapi.params.Depends"),
    ("py:class", "starlette.datastructures.QueryParams"),
    ("py:class", "fr.ReadOnly"),
    ("py:class", "function"),
    # The markers are type aliases, documented as data in api/views.rst. A
    # signature links them as classes, which never resolves to data.
    ("py:class", "fastapi_restly.views.ViewID"),
    ("py:class", "fastapi_restly.views.ViewResponse"),
    ("py:class", "fastapi_restly.views.ViewListResponse"),
    ("py:class", "fastapi_restly.views.ViewCreate"),
    ("py:class", "fastapi_restly.views.ViewUpdate"),
    # Python 3.10 only: Self comes from typing_extensions there, not typing.
    ("py:class", "typing_extensions.Self"),
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
    "pytest_fixtures": "testing.html",
    "the_handle_design": "lifecycle.html",
    "howto_override_endpoints": "lifecycle.html",
    "tutorial_overview": "tutorial.html",
    "getting_started": "tutorial.html",
    "api_details": "view_reference.html",
    "patterns": "guide.html",
    "examples": "guide.html",
    "howto_relationship_loading": "relationships.html",
    "howto_inheritance": "views.html",
    "howto_compose_views_with_mixins": "models.html",
    "howto_nested": "crud_routes.html",
    "howto_existing_project": "projects.html",
    "technical_details": "schemas.html",
    "howto_project_structure": "projects.html",
    "howto_models": "models.html",
    "class_based_views": "views.html",
    "rest_views": "crud_routes.html",
    "customize": "lifecycle.html",
    "howto_custom_schema": "schemas.html",
    "howto_response_schema": "responses.html",
    "howto_relationship_idschema": "relationships.html",
    "howto_query_modifiers": "filtering.html",
    "howto_error_responses": "errors.html",
    "howto_current": "context.html",
    "current": "context.html",
    "howto_testing": "testing.html",
    "howto_openapi": "openapi.html",
    "howto_react_admin": "react_admin.html",
    "howto_typing": "typing.html",
    "user_guide": "guide.html",
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
    "logo": {
        "text": "FastAPI-Restly",
        "image_light": "_static/fr-monogram.svg",
        "image_dark": "_static/fr-monogram-dark.svg",
    },
    "github_url": "https://github.com/rjprins/fastapi-restly",
    "use_edit_page_button": True,
    "show_toc_level": 2,
    "navigation_depth": 3,
    "icon_links": [],
    # Keep the three sections (Tutorial, Guide, API) and Blog visible. About
    # stays in the "More" dropdown.
    "header_links_before_dropdown": 4,
    # Version dropdown. json_url is absolute so frozen snapshots read the same
    # canonical list and surface versions published after they were built.
    "switcher": {"json_url": f"{SITE_URL}switcher.json", "version_match": DOCS_VERSION},
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
    "default_mode": "auto",
}
html_static_path = ["_static"]
# CNAME must ship in every publish: ghp-import replaces the gh-pages branch
# wholesale, and GitHub Pages drops the custom domain if the file disappears.
html_extra_path = ["robots.txt", "CNAME"]
html_logo = "_static/fr-monogram.svg"
html_favicon = "_static/favicon.svg"
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
            context.get("metatags", "")
            + '<meta name="robots" content="noindex, follow">\n'
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


def _skip_envelope_alias(app, what, name, obj, skip, options):
    # NumberedPagination.envelope and NoPagination.envelope hold a model class,
    # which autodoc renders as a bare "alias of ..." and drops the setting's
    # comment. The class docstrings describe the setting instead.
    if name == "envelope" and isinstance(obj, type):
        return True
    return None


def _short_class_defaults(app, what, name, obj, options, signature, annotation):
    # a class default, such as a settings dataclass's envelope, would print
    # as "<class 'fastapi_restly._pagination.PaginatedEnvelope'>"
    if signature:
        signature = re.sub(r"<class '(?:[\w.]+\.)?(\w+)'>", r"\1", signature)
    return signature, annotation


# -- llms.txt ----------------------------------------------------------------
# An index for AI agents (https://llmstxt.org), written from the Guide front
# page: its intro, "How Restly works", and one line per page in each group.
# The links go to the HTML pages, because several pages include their code
# from example-projects/, and their Markdown sources hold only the directive.

_LLMS_OVERVIEW = "How Restly works"
_ROLE = re.compile(r"\{[\w:]+\}`([^`<]*?)\s*(?:<[^>]*>)?`")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def _llms_page_url(app, docname):
    return app.config.html_baseurl + app.builder.get_target_uri(docname)


def _llms_link_url(app, target):
    """The absolute URL of a MyST link target, or None if it does not resolve."""
    if target.startswith(("http://", "https://")):
        return target
    if target.startswith("#"):
        label = app.env.get_domain("std").labels.get(target[1:])
        if label is None:
            return None
        docname, labelid, _ = label
        return f"{_llms_page_url(app, docname)}#{labelid}"
    return _llms_page_url(app, Path(target).with_suffix("").as_posix())


def _llms_markdown(app, text):
    """Plain Markdown: a role becomes code, and a link gets an absolute URL."""
    text = _ROLE.sub(lambda m: f"`{m.group(1)}`", text)

    def link(m):
        url = _llms_link_url(app, m.group(2))
        if url is None:
            sphinx_logging.getLogger(__name__).warning(
                "llms.txt: cannot resolve the link target %r", m.group(2)
            )
            return m.group(1)
        return f"[{m.group(1)}]({url})"

    return _LINK.sub(link, text).strip()


def _llms_list_items(body):
    """The items of the first Markdown list in body, each on one line."""
    items = []
    for line in body.splitlines():
        if line.startswith("- "):
            items.append(line[2:].strip())
        elif line.startswith("  ") and items:
            items[-1] += " " + line.strip()
        elif line.strip() and items:
            break
    return items


def _llms_toctree_section(app, heading, root):
    lines = [f"## {heading}", ""]
    for docname in [root, *app.env.toctree_includes.get(root, [])]:
        title = app.env.titles[docname].astext()
        lines.append(f"- [{title}]({_llms_page_url(app, docname)})")
    return lines + [""]


def _write_llms_txt(app, exception):
    if exception or app.builder.format != "html":
        return
    logger = sphinx_logging.getLogger(__name__)

    # The first paragraph of the landing page, below its title.
    index = Path(app.srcdir, "index.md").read_text(encoding="utf-8")
    summary = index.split("\n\n")[1].replace("\n", " ")

    guide = Path(app.srcdir, "guide.md").read_text(encoding="utf-8")
    guide = re.sub(r"^```\{toctree\}.*?^```\n", "", guide, flags=re.M | re.S)
    guide = re.sub(r"^\([\w-]+\)=\n", "", guide, flags=re.M)
    preamble, *sections = re.split(r"^## (.+)\n", guide, flags=re.M)
    intro = preamble.split("\n", 1)[1]  # without the "# Guide" title

    head = [f"# {app.config.project}", "", f"> {summary}", ""]
    head += [_llms_markdown(app, intro), ""]
    groups = []
    mapped = set()
    for heading, body in zip(sections[::2], sections[1::2]):
        if heading == _LLMS_OVERVIEW:
            head += [_llms_markdown(app, body), ""]
            continue
        groups += [f"## {heading}", ""]
        for item in _llms_list_items(body):
            target = _LINK.match(item)
            if target and not target.group(2).startswith(("http", "#")):
                mapped.add(Path(target.group(2)).with_suffix("").as_posix())
            groups.append(f"- {_llms_markdown(app, item)}")
        groups.append("")
    if _LLMS_OVERVIEW not in sections:
        logger.warning("llms.txt: guide.md has no %r section", _LLMS_OVERVIEW)
    missing = set(app.env.toctree_includes.get("guide", [])) - mapped
    if missing:
        logger.warning(
            "llms.txt: the Guide front page has no line for: %s",
            ", ".join(sorted(missing)),
        )

    lines = head + _llms_toctree_section(app, "Tutorial", "tutorial") + groups
    lines += _llms_toctree_section(app, "API", "api_reference")
    text = "\n".join(lines).rstrip() + "\n"
    Path(app.outdir, "llms.txt").write_text(text, encoding="utf-8")


def setup(app):
    app.connect("build-finished", _write_llms_txt)
    app.connect("autodoc-skip-member", _skip_envelope_alias)
    app.connect("autodoc-process-signature", _short_class_defaults)
    app.connect("html-page-context", _canonicalize_index_page)
    app.connect("html-page-context", _noindex_snapshots)
    app.connect("build-finished", _canonicalize_sitemap_index, priority=900)
