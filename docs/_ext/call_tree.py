"""A call tree: the steps of a request as a nested list that the reader can fold.

The source is one MyST bullet list. Each item starts with a step: a call in
code, such as `` `POST /` `` or ``{meth}`handle_create(data) <...>` ``, with an
optional note after it, or a step in plain words. The ``{override}`` role
links a method as ``{meth}`` does, and marks it as a method that the reader
can override. An item with a nested list is open; an item that only holds
text below its step is closed, so the text is there for whoever wants it.

    :::{call-tree}
    - `POST /`
      - {override}`create_endpoint(data) <...>` The HTTP contract.
        - Save: flush, then refresh the object.

          A longer explanation, folded away.
    :::

The tree is plain HTML: ``<details>`` folds without JavaScript, and CSS draws
the lines (see ``custom.css``).
"""

from docutils import nodes
from sphinx import addnodes
from sphinx.util.docutils import SphinxDirective


class call_tree_list(nodes.General, nodes.Element):
    pass


class call_tree_item(nodes.General, nodes.Element):
    pass


class call_tree_details(nodes.General, nodes.Element):
    pass


class call_tree_summary(nodes.General, nodes.Element):
    pass


def _visit(tag):
    def visit(self, node):
        attributes = {"open": "open"} if node.get("open") else {}
        self.body.append(self.starttag(node, tag, "", **attributes))

    def depart(self, node):
        self.body.append(f"</{tag}>")

    return visit, depart


def _override_role(name, rawtext, text, lineno, inliner, options=None, content=None):
    meth = inliner.document.settings.env.get_domain("py").role("meth")
    xref, messages = meth(
        "py:meth", rawtext, text, lineno, inliner, options or {}, content or []
    )
    badge = nodes.inline("", "override", classes=["call-tree-badge"])
    return [nodes.inline("", "", *xref, badge, classes=["call-tree-call"])], messages


def _is_call(node):
    return isinstance(node, (nodes.literal, addnodes.pending_xref)) or (
        isinstance(node, nodes.inline) and "call-tree-call" in node["classes"]
    )


def _step(paragraph):
    """The step of an item: its call and the note after it, or plain words."""
    children = [child.deepcopy() for child in paragraph.children]
    if not children or not _is_call(children[0]):
        return nodes.inline(
            "", "", *children, classes=["call-tree-step", "call-tree-words"]
        )
    note = children[1:]
    if note and isinstance(note[0], nodes.Text):
        note[0] = nodes.Text(note[0].astext().lstrip())
    step = nodes.inline("", "", children[0], classes=["call-tree-step"])
    if any(child.astext().strip() for child in note):
        step += nodes.inline("", "", *note, classes=["call-tree-note"])
    return step


class CallTree(SphinxDirective):
    has_content = True

    def run(self):
        parsed = nodes.Element()
        self.state.nested_parse(self.content, self.content_offset, parsed)
        if len(parsed) != 1 or not isinstance(parsed[0], nodes.bullet_list):
            raise self.error("call-tree: the content must be one bullet list.")
        tree = nodes.container(classes=["call-tree"])
        tree += self._list(parsed[0])
        return [tree]

    def _list(self, bullet_list):
        tree_list = call_tree_list()
        for item in bullet_list.children:
            tree_list += self._item(item)
        return tree_list

    def _item(self, list_item):
        if not list_item.children or not isinstance(list_item[0], nodes.paragraph):
            raise self.error("call-tree: each item must start with a step in one line.")
        step = _step(list_item[0])
        tree_item = call_tree_item()
        below = list_item.children[1:]
        if not below:
            tree_item += step
            return tree_item

        has_list = any(isinstance(child, nodes.bullet_list) for child in below)
        details = call_tree_details(open=has_list)
        details += call_tree_summary("", step)
        for child in below:
            if isinstance(child, nodes.bullet_list):
                details += self._list(child)
            else:
                details += nodes.container(
                    "", child.deepcopy(), classes=["call-tree-text"]
                )
        tree_item["classes"].append("call-tree-fold")
        tree_item += details
        return tree_item


def setup(app):
    app.add_node(call_tree_list, html=_visit("ul"))
    app.add_node(call_tree_item, html=_visit("li"))
    app.add_node(call_tree_details, html=_visit("details"))
    app.add_node(call_tree_summary, html=_visit("summary"))
    app.add_role("override", _override_role)
    app.add_directive("call-tree", CallTree)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
