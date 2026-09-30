# Known Issues

These problems can still happen with the current release. Each entry says
which versions are affected and what to do.

(known-issue-type-checking-nameerror)=
## `NameError` on Python 3.14 for a `TYPE_CHECKING` import

On Python 3.14, importing a model module can fail with `NameError` for a class
that you import only under `if TYPE_CHECKING:`. This happens with SQLAlchemy
2.0.44 and earlier, for a relationship like this one:

```python
# shop/author.py
from typing import TYPE_CHECKING

from sqlalchemy.orm import Mapped, relationship

import fastapi_restly as fr

if TYPE_CHECKING:
    from shop.book import Book


class Author(fr.IDBase):
    name: Mapped[str]
    books: Mapped[list[Book]] = relationship(
        back_populates="author", default_factory=list
    )
```

```text
NameError: name 'Book' is not defined
```

The traceback includes SQLAlchemy's `_apply_dataclasses_to_any_class`. To fix
it, upgrade SQLAlchemy to 2.0.45 or later.

Only mapped dataclasses are affected: models that inherit
{class}`~sqlalchemy.orm.MappedAsDataclass`. Restly's
{class}`fr.DataclassBase <fastapi_restly.models.DataclassBase>`,
{class}`fr.IDBase <fastapi_restly.models.IDBase>`,
{class}`fr.models.IDMixin <fastapi_restly.models.IDMixin>` and
{class}`fr.TimestampsMixin <fastapi_restly.models.TimestampsMixin>` all
inherit it.

If you cannot upgrade SQLAlchemy yet, write the annotation as you would for
Python 3.13. Add `from __future__ import annotations` at the top of the module,
or put the name in quotes, as in `Mapped[list["Book"]]`.
