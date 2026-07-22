# Sphinx configuration for the Restly blog, published at /blog/.
#
# The blog is a separate Sphinx project on purpose: the site root rebuilds
# only from release tags, while blog posts must publish on any push to main.
# It deploys to the gh-pages blog/ subdirectory (keep_files), so it stays
# out of the versioned docs entirely and is never frozen into snapshots.

from importlib.metadata import version as _package_version

project = "FastAPI-Restly"
copyright = "2026, Rutger Prins"
author = "Rutger Prins"
release = _package_version("fastapi-restly")

SITE_URL = "https://www.fastapi-restly.org/"
BLOG_URL = f"{SITE_URL}blog/"

html_baseurl = BLOG_URL
sitemap_url_scheme = "{link}"

extensions = [
    "myst_parser",
    "ablog",
    "sphinx_sitemap",
    "sphinx_copybutton",
    "sphinx_design",
]

myst_heading_anchors = 3
myst_enable_extensions = ["colon_fence", "substitution"]

# -- ABlog -------------------------------------------------------------------

blog_title = "FastAPI-Restly Blog"
blog_baseurl = BLOG_URL
blog_path = "posts"
blog_post_pattern = ["posts/*.md", "posts/*/*.md"]
blog_authors = {"rutger": ("Rutger Prins", "https://github.com/rjprins")}
blog_default_author = "rutger"
# Full-text feed: aggregators such as Planet Python republish complete posts.
blog_feed_fulltext = True

# -- HTML output -------------------------------------------------------------

html_theme = "pydata_sphinx_theme"
html_theme_options = {
    "logo": {"text": "FastAPI-Restly Blog"},
    "github_url": "https://github.com/rjprins/fastapi-restly",
    "use_edit_page_button": True,
    "icon_links": [],
    "external_links": [
        {"name": "Docs", "url": SITE_URL},
    ],
    # No version switcher: the blog is unversioned.
    "navbar_end": ["theme-switcher", "navbar-icon-links"],
    "secondary_sidebar_items": ["page-toc", "newsletter-link"],
}
html_context = {
    "github_user": "rjprins",
    "github_repo": "fastapi-restly",
    "github_version": "main",
    "doc_path": "blog",
}
# Branding is shared with the docs build; templates stay local because the
# docs newsletter template links by docname, which does not exist here.
html_static_path = ["../docs/_static"]
templates_path = ["_templates"]
html_logo = "../docs/_static/restly-cat.png"
html_css_files = ["custom.css"]
html_js_files = ["newsletter.js"]
