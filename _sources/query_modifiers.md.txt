### Filtering on relations

By default, filtering on relations between models is supported if the relation is defined
in both the SQLAlchemy model and the corresponding Pydantic schema.

For example, the following query:

```
/api/orders/?filter[user.name]=Henk
```

is possible if the models are defined like this:

#### SQLAlchemy models:

```python
class User(DeclarativeBase):
    __tablename__ = "user"
    id = mapped_column(primary_key=True)
    name = mapped_column(String)

class Order(DeclarativeBase):
    __tablename__ = "order"
    id = mapped_column(primary_key=True)
    user_id = mapped_column(ForeignKey("user.id"))
    user = relationship("User")
```

#### Pydantic schemas:

```python
class UserSchema(BaseModel):
    name: str

class OrderSchema(BaseModel):
    user: UserSchema  # or: user: UserSchema | None
```

In this setup, filtering on `filter[user.name]=Henk` will automatically:

* Join the `user` table,
* Validate `"Henk"` as a valid value for `UserSchema.name`,
* Filter results using `user.name = 'Henk'`.

> **Note:**
>
> * Nested schemas can be optional, e.g. `UserSchema | None`.
> * Lists of nested schemas (e.g. `list[UserSchema]`) are **not** supported.
