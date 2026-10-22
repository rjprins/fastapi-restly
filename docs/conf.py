# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

import dataclasses
import enum
import importlib
import inspect
import os
import re
import sys
import types
import typing
from importlib.metadata import version as _package_version
from pathlib import Path
from xml.etree import ElementTree

import sphinx.ext.autodoc as sphinx_autodoc
import sphinx.util.inspect as sphinx_inspect
import sphinx.util.typing as sphinx_typing
from docutils import nodes
from sphinx.errors import PycodeError
from sphinx.ext.autodoc.importer import get_class_members
from sphinx.pycode import ModuleAnalyzer
from sphinx.util import logging as sphinx_logging
from sqlalchemy.orm import MappedAsDataclass
from sqlalchemy.orm.properties import MappedColumn

import fastapi_restly.db as fr_db
from fastapi_restly.schemas._base import readonly_marker, writeonly_marker
from fastapi_restly.views._view_types import _ViewType

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
    ("py:class", "function"),
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
# Members in the order of the source, or of __all__ in a package module.
autodoc_member_order = "bysource"

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


# -- API reference -----------------------------------------------------------
# Autodoc writes a type with the repr of its Annotated metadata, and a default
# value with its repr. For Restly's aliases and sentinels that gives text that
# is not Python, such as "FieldInfo(...)" or "<object object>", and Sphinx then
# cannot parse the signature. Autodoc has no event for this, so conf.py wraps
# the Sphinx functions that write types and values. The rest of this section
# uses autodoc's events and documenter classes.

_RESTLY_MODULES = [
    importlib.import_module(f"fastapi_restly.{name}")
    for name in (
        "clauses",
        "db",
        "exc",
        "models",
        "objects",
        "query",
        "schemas",
        "views",
        "testing",
    )
]
_RESTLY_PUBLIC = {
    id(getattr(module, name)) for module in _RESTLY_MODULES for name in module.__all__
}
_stringify_annotation = sphinx_typing.stringify_annotation
_object_description = sphinx_inspect.object_description


def _alias_name(annotation):
    """The public name of the Restly alias that an annotation spells, and the
    type in its brackets, or None. ReadOnly[datetime] gives
    ("fastapi_restly.schemas.ReadOnly", datetime)."""
    if typing.get_origin(annotation) is not typing.Annotated:
        return None
    if annotation == fr_db.SessionDep:
        return "fastapi_restly.db.SessionDep", None
    if annotation == fr_db.AsyncSessionDep:
        return "fastapi_restly.db.AsyncSessionDep", None
    inner = typing.get_args(annotation)[0]
    for meta in annotation.__metadata__:
        if meta is readonly_marker:
            return "fastapi_restly.schemas.ReadOnly", inner
        if meta is writeonly_marker:
            return "fastapi_restly.schemas.WriteOnly", inner
        if isinstance(meta, _ViewType):
            return f"fastapi_restly.views.{meta.name}", None
    return None


def _restly_stringify_annotation(
    annotation, /, mode="fully-qualified-except-typing", **kwargs
):
    alias = _alias_name(annotation)
    if alias is None:
        return _stringify_annotation(annotation, mode, **kwargs)
    name, inner = alias
    if mode == "smart":
        name = f"~{name}"
    if inner is None:
        return name
    return f"{name}[{_restly_stringify_annotation(inner, mode, **kwargs)}]"


def _restly_object_description(obj, **kwargs):
    if obj is type(None):
        return "None"
    if isinstance(obj, type):
        return obj.__name__
    if isinstance(obj, types.UnionType):  # a type alias such as ReadScope
        return " | ".join(map(_restly_object_description, typing.get_args(obj)))
    if type(obj) is object:  # a sentinel for "not given"
        return "..."
    if isinstance(obj, MappedColumn):
        return "mapped_column(...)"
    if dataclasses.is_dataclass(obj) and id(type(obj)) in _RESTLY_PUBLIC:
        # Settings such as NumberedPagination: only the fields that differ
        # from the defaults, so the default reads NumberedPagination().
        default = type(obj)()
        changed = [
            f"{field.name}={_restly_object_description(getattr(obj, field.name))}"
            for field in dataclasses.fields(obj)
            if getattr(obj, field.name) != getattr(default, field.name)
        ]
        return f"{type(obj).__name__}({', '.join(changed)})"
    return _object_description(obj, **kwargs)


sphinx_typing.stringify_annotation = _restly_stringify_annotation
sphinx_inspect.stringify_annotation = _restly_stringify_annotation
sphinx_inspect.object_description = _restly_object_description
sphinx_autodoc.stringify_annotation = _restly_stringify_annotation
sphinx_autodoc.object_description = _restly_object_description


def _resolve_alias_as_data(app, env, node, contnode):
    # A type in a signature links as a class. ReadOnly, SessionDep and the
    # view markers are documented as data, so look them up as data as well.
    if node.get("refdomain") != "py" or node.get("reftype") != "class":
        return None
    return env.get_domain("py").resolve_xref(
        env, node["refdoc"], app.builder, "data", node["reftarget"], node, contnode
    )


def _read_reexported_comments(analyzer):
    """Add the #: comments of the names that a public module re-exports.

    Autodoc reads a module's #: comments only from that module, so a name that
    is defined in a private module, such as ReadScope, would get no entry.
    """
    module = sys.modules.get(analyzer.modname)
    if module not in _RESTLY_MODULES:
        return
    for name in module.__all__:
        if ("", name) in analyzer.attr_docs:
            continue
        value = getattr(module, name)
        for source in list(sys.modules.values()):
            source_name = getattr(source, "__name__", "")
            if not source_name.startswith("fastapi_restly."):
                continue
            if source is module or getattr(source, name, None) is not value:
                continue
            docs = ModuleAnalyzer.for_module(source_name).find_attr_docs()
            if ("", name) in docs:
                analyzer.attr_docs["", name] = docs["", name]
                break


_analyze = ModuleAnalyzer.analyze


def _analyze_with_reexported_comments(self):
    if self._analyzed:
        return
    _analyze(self)
    _read_reexported_comments(self)


ModuleAnalyzer.analyze = _analyze_with_reexported_comments


def _has_attribute_comment(cls, name):
    for klass in cls.__mro__:
        try:
            docs = ModuleAnalyzer.for_module(klass.__module__).find_attr_docs()
        except PycodeError:
            continue
        if (klass.__qualname__, name) in docs:
            return True
    return False


class _ClassSettingDocumenter(sphinx_autodoc.AttributeDocumenter):
    """A class attribute that holds a class, such as NumberedPagination's
    envelope, as an attribute with its #: comment. Autodoc would document it as
    a bare "alias of" the class, without the comment."""

    objtype = "classsetting"
    directivetype = "attribute"
    priority = sphinx_autodoc.ClassDocumenter.priority + 1

    @classmethod
    def can_document_member(cls, member, membername, isattr, parent):
        return (
            isinstance(member, type)
            and isinstance(parent, sphinx_autodoc.ClassDocumenter)
            and _has_attribute_comment(parent.object, membername)
        )


def _private_bases(cls):
    """The private classes in cls.__mro__ before its first public base."""
    private = []
    for klass in cls.__mro__[1:]:
        if not klass.__name__.startswith("_"):
            break
        private.append(klass)
    return private


def _defining_class(cls, name):
    for klass in cls.__mro__:
        if name in vars(klass) or name in inspect.get_annotations(klass):
            return klass
    return None


class _ClassDocumenter(sphinx_autodoc.ClassDocumenter):
    """A class also shows the members of a private base, such as assert_status
    from the test clients' _StatusAssertions. A private base has no entry, so
    autodoc would show these members nowhere."""

    def get_object_members(self, want_all):
        check, members = super().get_object_members(want_all)
        private = _private_bases(self.object)
        if not want_all or self.options.inherited_members or not private:
            return check, members
        own = {member.__name__ for member in members}
        everything = get_class_members(
            self.object,
            self.objpath,
            self.get_attr,
            self.config.autodoc_inherit_docstrings,
        )
        for name, member in everything.items():
            if name not in own and _defining_class(self.object, name) in private:
                members.append(member)
        return check, members

    def sort_members(self, documenters, order):
        # Autodoc reads the source order from the module that it documents,
        # which is the package for a re-exported class. Read it from the
        # module that defines each member.
        if order != "bysource":
            return super().sort_members(documenters, order)

        def source_order(entry):
            name = entry[0].name.split("::")[1].rsplit(".", 1)[-1]
            klass = _defining_class(self.object, name) or self.object
            analyzer = ModuleAnalyzer.for_module(klass.__module__)
            analyzer.analyze()
            position = analyzer.tagorder.get(f"{klass.__qualname__}.{name}", 1 << 30)
            return self.object.__mro__.index(klass), position

        documenters.sort(key=source_order)
        return documenters


def _skip_member(app, what, name, obj, skip, options):
    # A view's Action is fr.Action, which the module documents already.
    if what == "class" and name == "Action" and isinstance(obj, type):
        return True
    # Pydantic's own setting, with Pydantic's own text.
    if what == "class" and name == "model_config":
        return True
    return None


def _hide_class_signature(app, what, name, obj, options, signature, annotation):
    # An enum shows Enum's own signature. A mixin for mapped dataclasses shows
    # the signature of the mixin, but a model's constructor is another one.
    if what == "class" and isinstance(obj, type):
        if issubclass(obj, enum.Enum) or (
            issubclass(obj, MappedAsDataclass) and "__mapper__" not in vars(obj)
        ):
            return "", annotation
    return None


def _public_bases(app, name, obj, options, bases):
    # Leave out object, Generic[...] with Restly's own type variables, and the
    # private classes, which have no entry to link to.
    def public(base):
        origin = typing.get_origin(base) or base
        if origin in (object, typing.Generic):
            return False
        if getattr(origin, "__module__", "").startswith("fastapi_restly"):
            return id(origin) in _RESTLY_PUBLIC
        return not getattr(origin, "__name__", "_").startswith("_")

    bases[:] = [base for base in bases if public(base)]


def _drop_empty_bases(app, domain, objtype, contentnode):
    # A class whose bases all went leaves an empty "Bases:" line.
    if domain != "py" or objtype not in ("class", "exception"):
        return
    first = next(contentnode.findall(nodes.paragraph), None)
    if first is not None and first.astext().strip() == "Bases:":
        first.parent.remove(first)


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
    app.add_autodocumenter(_ClassDocumenter, override=True)
    app.add_autodocumenter(_ClassSettingDocumenter)
    app.connect("autodoc-skip-member", _skip_member)
    app.connect("autodoc-process-signature", _hide_class_signature)
    app.connect("autodoc-process-bases", _public_bases)
    app.connect("object-description-transform", _drop_empty_bases)
    app.connect("missing-reference", _resolve_alias_as_data)
    app.connect("html-page-context", _canonicalize_index_page)
    app.connect("html-page-context", _noindex_snapshots)
    app.connect("build-finished", _canonicalize_sitemap_index, priority=900)
