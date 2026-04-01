# Filtering on Relations

Both V1 and V2 support filtering on fields of a related model using dot notation.
The relation must be defined in both the SQLAlchemy model (as a `relationship`) and
the Pydantic schema (as a nested schema field).

For a full operator and feature reference, see
[How-To: Filter, Sort, and Paginate Lists](howto_query_modifiers.md).

---

## Example

Given this URL:

```text
GET /orders/?filter[user.name]=Alice    # V1 syntax
GET /orders/?user.name=Alice            # V2 syntax
```

The framework automatically joins the `user` table and filters on `user.name`.

### SQLAlchemy models

```python
import fastapi_restly as fr
from sqlalchemy import String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

class User(fr.IDBase):
    __tablename__ = "user"
    name: Mapped[str] = mapped_column(String)

class Order(fr.IDBase):
    __tablename__ = "order"
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    user: Mapped[User] = relationship("User")
```

### Pydantic schemas

```python
from pydantic import BaseModel

class UserSchema(BaseModel):
    name: str

class OrderSchema(BaseModel):
    user: UserSchema  # or: user: UserSchema | None
```

### What happens

When `filter[user.name]=Alice` (V1) or `user.name=Alice` (V2) is received:

1. The `user` table is joined.
2. `"Alice"` is validated as a valid value for `UserSchema.name`.
3. The query is filtered with `user.name = 'Alice'`.

---

## Constraints

- Nested schemas can be optional: `user: UserSchema | None`.
- Deep nesting is supported: `filter[blog.author.name]=Alice`.
- Lists of nested schemas (`list[UserSchema]`) are **not** supported.
- V2 relation params use the alias name of each path segment when aliases are defined.
