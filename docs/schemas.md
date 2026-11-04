# Schemas & Validation

:::{note}
Restly says **schema** for a Pydantic class and **model** for a SQLAlchemy
class. A `User` model is the database table. `UserSchema`, the main schema of
the view, is the public API.
:::

A view has several schemas, one for each use. Together they are "the view's
schemas". The main schema is the view's
{attr}`schema <fastapi_restly.views.BaseRestView.schema>`, a Pydantic class
that lists the fields of the resource. When you register the view, Restly
builds the other schemas from it:

| Schema | Setting | Generated name | Used for |
|---|---|---|---|
| main schema | {attr}`schema <fastapi_restly.views.BaseRestView.schema>` | `UserSchema` | the fields of the resource |
| response schema | {attr}`response_schema <fastapi_restly.views.BaseRestView.response_schema>` | `UserResponse` | everything that goes out: the main schema without the `WriteOnly` fields |
| list response schema | {attr}`list_response_schema <fastapi_restly.views.BaseRestView.list_response_schema>` | `UserListResponse` | the response of the list route: the response schema in an envelope |
| create schema | {attr}`create_schema <fastapi_restly.views.BaseRestView.create_schema>` | `UserCreate` | the body of `POST`: the main schema without the `ReadOnly` fields |
| update schema | {attr}`update_schema <fastapi_restly.views.BaseRestView.update_schema>` | `UserUpdate` | the body of `PATCH`: the main schema without the `ReadOnly` fields, with every other field optional |
| list params schema | {attr}`list_params_schema <fastapi_restly.views.BaseRestView.list_params_schema>` | `UserListParams` | the keys that the list route reads from the URL: filters, sort and page |

The response, create and update schemas are subclasses of the main schema. So
each one keeps the field types, the aliases and the validators of the main
schema. Two markers decide which schema keeps a field: {data}`fr.ReadOnly
<fastapi_restly.schemas.ReadOnly>` keeps it out of the create and update
schemas, and {data}`fr.WriteOnly <fastapi_restly.schemas.WriteOnly>` keeps it
out of the response schema. The filters and sort keys of the list route come
from the response schema too; [Filtering, Sorting & Pagination](filtering.md)
covers them.

You can set each schema on the view yourself, through its setting. Restly then
uses your class as you wrote it. A view without a main schema gets one that
Restly generates from the model.

Find the section for your goal:

| Goal | Tool |
|---|---|
| A public API that differs from the table | [Write the schema](#write-or-generate) |
| An input field that responses never show | [`WriteOnly`](#readonly-and-writeonly) |
| A field that the server owns and clients cannot set | [`ReadOnly`](#readonly-and-writeonly) |
| A field set on create and frozen afterwards | [Your own create and update schemas](#explicit-write-schemas) |
| A rule that a value must follow | [Validation](#validation) |
| Times from clients in other time zones | [`UTCDatetime`](#utc-times) |
| Other field names in the JSON | [Aliases](#field-aliases) |
| A response that differs from the input | [Your own response schema](#own-response-schema) |
| Clear type names in OpenAPI and generated clients | [Name your schemas](#name-your-schemas) |
| A custom route that returns the view's schemas | [Name the schemas in a custom route](#name-the-view-schemas) |
| A foreign key, or a relationship as an id or an object | [Relationships](relationships.md) |

(write-or-generate)=
## Generate the schema or write it

Without a schema, Restly generates the main schema from the model. This is
enough when the table is already the API you want:

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
```

The generated schema has one field for each column, and no relationships.
Restly marks `id`, `created_at`, `updated_at` and each column with
`init=False` as `ReadOnly`; see
[How Restly generates a schema](#auto-generated-schemas) for the details.

Write the schema yourself when the API differs from the table: other field
names, fields that only go one way, references to other rows, or rules for
the values. Write it also when the field names must stay the same while the
table changes. Set it on the view:

```python
class UserSchema(fr.IDSchema):
    name: str
    email: str


@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserSchema
```

Restly writes each field of a request body to the model attribute with the
same Python name. So name a field after its column, and use an
[alias](#field-aliases) when the JSON needs another name.

:::{admonition} Details
:class: details

A SQLModel table class cannot be the
{attr}`schema <fastapi_restly.views.BaseRestView.schema>`, the
{attr}`create_schema <fastapi_restly.views.BaseRestView.create_schema>` or the
{attr}`update_schema <fastapi_restly.views.BaseRestView.update_schema>` of a view.
A table class does not validate its input, also on a plain FastAPI route, and
SQLModel says not to inherit from it. So the view fails when it registers. Leave
`schema` out, or set a class without `table=True`.

The table class itself can be the
{attr}`response_schema <fastapi_restly.views.BaseRestView.response_schema>`. A
subclass of it cannot, because OpenAPI would show no required fields.

A table class as a field of a schema, such as `team: Team`, does not validate
either. Plain FastAPI does the same, and Restly does not check for it. Use a data
model for that field.

The schema that Restly generates takes its fields from the columns. It does not
take the limits in `Field(...)` or the validators of the table class. To keep
them, put them on a data model, and let the table and the schema inherit from
it, as SQLModel recommends:

```python
class HeroBase(SQLModel):
    name: str = Field(max_length=50)


class Hero(HeroBase, table=True):
    id: int | None = Field(default=None, primary_key=True)


class HeroSchema(HeroBase):
    id: fr.ReadOnly[int]


@fr.include_view(app)
class HeroView(fr.RestView):
    prefix = "/heroes"
    model = Hero
    schema = HeroSchema
```
:::

(schema-base-classes)=
## Choose a base class

Restly has three base classes for a schema:

- {class}`fr.IDSchema <fastapi_restly.schemas.IDSchema>` adds a read-only
  `id` field, as `id: fr.ReadOnly[Any]`. Most schemas use it. The `id` is in
  the responses, and the create and update bodies leave it out.
- {class}`fr.BaseSchema <fastapi_restly.schemas.BaseSchema>` is the class
  under `IDSchema`, without the `id`. Use it to declare every field
  yourself, `id` included. Mark the `id` `ReadOnly` if clients must not send
  it.
- {class}`fr.TimestampsSchemaMixin <fastapi_restly.schemas.TimestampsSchemaMixin>`
  adds read-only `created_at` and `updated_at` fields, in UTC. Put it before
  the base class.

```python
class UserSchema(fr.TimestampsSchemaMixin, fr.IDSchema):
    name: str
    email: str
```

:::{admonition} Details
:class: details

`BaseSchema` is a Pydantic `BaseModel` with `from_attributes=True`, so it can
read the attributes of an ORM object. It writes a NaN or an infinity in a
response as text; see [Keep NaN and infinity](#nan-and-infinity). It also
checks that the markers are used in the right place; see
[the next section](#readonly-and-writeonly).

A create or update schema that you set on the view can also be a plain
`pydantic.BaseModel`. Its fields can still use `ReadOnly`,
`WriteOnly` and {class}`fr.MustExist <fastapi_restly.schemas.MustExist>`.

`fr.IDSchema[User]`, with the model as a type parameter, gives the schema the
model's type; [Type Annotations](typing.md) explains when that helps.
As the type of a field, `fr.IDSchema[User]` is a reference to another row;
see [Relationships](relationships.md).
:::

(readonly-and-writeonly)=
## Hide a field from requests or from responses

{data}`fr.ReadOnly[T] <fastapi_restly.schemas.ReadOnly>` marks a field that
only goes out. The create and update bodies leave it out, so a client cannot
set it. A value that a client sends for it is ignored:

```python
class UserSchema(fr.IDSchema):
    name: str
    created_by_id: fr.ReadOnly[int]
```

The server sets such a value. Set it on the model, with a column default, so
that every write path sets it; see
[Stamp the fields the server owns](#server-stamped-fields).

{data}`fr.WriteOnly[T] <fastapi_restly.schemas.WriteOnly>` marks a field that
only comes in. The create and update bodies accept it, and no response shows
it:

```python
class UserSchema(fr.IDSchema):
    email: str
    password: fr.WriteOnly[str]
```

Pydantic's secret types, such as `SecretStr`, work in a schema too. A write
stores the secret, and a response shows `**********`, as Pydantic does. So
a client sees that the field has a value, but not the value. Like a
`WriteOnly` field, such a field gets no filter and is not a sort key. In the
document of a JSON column, a secret field raises an error instead; see
[Store a Pydantic model in a JSON column](#json-document-columns).

:::{admonition} Details
:class: details

The two markers work in different places. This matters when you use a
schema outside a view:

- `WriteOnly` sets Pydantic's `exclude` on the field. So every serialization
  of the schema drops it: the CRUD responses, a FastAPI `response_model`, and
  your own `model_dump()` calls. This also holds in nested schemas, and the
  response schema in OpenAPI does not show the field.
- `ReadOnly` works only where Restly applies it: in the create and update
  bodies that Restly generates, and when Restly's object helpers build or
  update an ORM object. On a schema that you use yourself, a `ReadOnly`
  field validates and serializes like any other field. OpenAPI marks it
  `readOnly`.

A marker must be the outer type of the field. Inside a union or a container,
as in `Optional[WriteOnly[str]]`, `WriteOnly[str] | None` or
`list[WriteOnly[str]]`, the marker would do nothing. The same holds inside a
type alias, such as `type Secret = WriteOnly[str]`. So Restly rejects such a
field with a
{class}`RestlyConfigurationError <fastapi_restly.exc.RestlyConfigurationError>`.
For an optional write-only field, write `WriteOnly[Optional[str]]`.

A JSON column has its own rules for `ReadOnly` and `WriteOnly` fields; see
[Store a Pydantic model in a JSON column](#json-document-columns).
:::

(explicit-write-schemas)=
## Write the create and update schemas yourself

A {attr}`create_schema <fastapi_restly.views.BaseRestView.create_schema>` or
{attr}`update_schema <fastapi_restly.views.BaseRestView.update_schema>` that
you set on the view is the whole contract for its request. Restly writes every
field that it declares, except the fields that the class itself marks
`ReadOnly`. The markers on the main schema only shape the bodies that Restly
generates.

So you can set a field on create and freeze it afterwards. Mark it `ReadOnly`
on the main schema, and declare it in your own create schema:

```python
class CommentSchema(fr.IDSchema):
    body: str
    story_id: fr.ReadOnly[int]


class CommentCreate(fr.BaseSchema):
    body: str
    story_id: fr.MustExist[int, Story]


class CommentView(fr.AsyncRestView):
    prefix = "/comments"
    model = Comment
    schema = CommentSchema
    create_schema = CommentCreate
```

`POST /comments` sets `story_id`. The generated update schema leaves it out, so
`PATCH` ignores it.

Keep a field that the server stamps, such as a tenant id, out of every
create or update schema that you write. Otherwise a client can set it.

(validation)=
## Validate input

Each kind of rule has one place:

| Rule | Where it goes | What the client gets |
|---|---|---|
| The format or the range of one value | A Pydantic validator or a `Field` constraint on the schema | 422 |
| A field that a client cannot set | [`ReadOnly`](#readonly-and-writeonly), or [your own create and update schemas](#explicit-write-schemas) | The value is ignored |
| The shape of a JSON document | A Pydantic model as the type of the column; see [Store a Pydantic model in a JSON column](#json-document-columns) | 422 |
| A value that its column can hold, such as a number in the range of `Integer` | Nothing for the generic types of SQLAlchemy, a `Field` constraint for other types; see [Column validation](#column-validation) | 422, or 400 in a filter |
| A referenced row must exist | {class}`fr.MustExist <fastapi_restly.schemas.MustExist>` or a reference field; see [Relationships](#reference-exists) | 404 |
| A value must be unique | A unique constraint on the column; see [Errors](#default-exception-handling) | 409 |
| A rule that needs the database, the stored row or the caller | The `create` or `update` business method; see [Request Lifecycle & Hooks](#business-methods) | The error that you raise |

Whether the caller may run the action at all is not validation. It is a
policy, and it goes in
[`authorize`](#authorize-gate), which answers with 403.

### Value validation

Put a validator or a `Field` constraint on the main schema. The create
schema, the update schema and the response schema are subclasses of the main
schema, so all three run it:

```python
import re

import pydantic


class OrganizationSchema(fr.IDSchema):
    name: str = pydantic.Field(min_length=2)
    slug: str

    @pydantic.field_validator("slug")
    @classmethod
    def check_slug(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", value):
            raise ValueError("use lowercase letters, digits and hyphens")
        return value
```

A value that fails returns 422, with Pydantic's list of errors.

A rule that compares two fields, in a `model_validator`, works on the create
schema. An instance of the update schema holds only the fields that the client
sends, and the other fields are `None`. Check such a rule in `update`, where
you have the stored row too. Otherwise a `PATCH` that sends one of the two
fields can save a row that breaks the rule. The response schema runs the
validator too, so every read of that row, and every list that holds it, then
fails with a server error.

:::{admonition} Details
:class: details

The validator also runs in these places:

- In the update schema, the client may leave out each field. Pydantic does not
  validate a field that the client leaves out, so the validator does not
  run for it. Each field keeps its type, so `null` is valid only where the
  type allows `None`, as in the create schema. `{"slug": null}` returns 422
  before the validator runs, so the validator above never gets `None`. A
  validator with `mode="before"` runs before that check, so it still gets
  the client's `null`.
- The response schema runs the validator when Restly builds a response from a
  row. A row that breaks the rule, for example one that a script wrote, then
  fails with a server error.
- List filters run it on each filter value; see
  [Filter values go through the field's validation](#filter-value-validation).

A schema without the field does not run its validator. The create and update
schemas have no `ReadOnly` fields and no primary key that the server generates.
The response schema has no `WriteOnly` fields.

To check only what comes in, put the validator on a create or update schema
that you set on the view. The SaaS example does this for organizations
([`app/organizations/schemas.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/organizations/schemas.py)).
:::

(column-validation)=
### Column validation

Restly checks each value from a client against the type of its column,
before the database sees the value. It checks the generic integer and text
types of SQLAlchemy, so you do not write a validator for them:

- A number must fit the integer type: 16 bits for `SmallInteger`, 32 bits
  for `Integer`, from -2,147,483,648 to 2,147,483,647, and 64 bits for
  `BigInteger`.
- Text must not hold a NUL character (`\x00`).
- Text in a request body must fit the length of a `String(n)` column.

A value that does not fit gets 422 in a request body or in the path, and
400 in a filter. A reference id that does not fit gets 404, as for a row
that does not exist.

:::{admonition} Details
:class: details

The type of the column is the rule on every database. PostgreSQL refuses
such a value with an error, which would be a 500. SQLite stores it, so the
tests of an app on SQLite would not see the problem. PostgreSQL cannot store
a NUL character in text, so Restly refuses it on every database. A column
that holds bigger numbers needs `BigInteger`, such as a column with a Unix
time in milliseconds. A filter only compares a value with the column, and
PostgreSQL compares text of any length, so the length counts only in a
request body. A `CHAR` without a length holds one character, as in SQL.

Restly checks these types by their exact class: `Integer`, `SmallInteger`,
`BigInteger`, `String`, `Text`, `Unicode` and `UnicodeText`, and the same
types by their SQL names: `INTEGER`, `SMALLINT`, `BIGINT`, `VARCHAR`,
`NVARCHAR`, `CHAR`, `NCHAR` and `TEXT`. Any other type gets the value as it
is, as in SQLAlchemy:

- a type of a dialect, such as MySQL's `TINYINT`,
- a subclass of a type above, such as `Enum`,
- a {class}`~sqlalchemy.types.TypeDecorator`, such as SQLModel's
  `AutoString`,
- a type from `with_variant()`.

The database then checks the value. On PostgreSQL, a value that does not
fit is an error, and the client gets a 500. For a 422, put the limit on the
field of the schema. Its validation runs on filter values too; see
[Filter values go through the field's validation](#filter-value-validation).

| Column | Field of the schema |
|---|---|
| Text with a length, in a type that Restly does not check | `Annotated[str, Field(max_length=50)]` |
| A smaller integer, such as MySQL's `TINYINT` | `Annotated[int, Field(ge=-128, le=127)]` |
| `Numeric(5, 2)` | `Annotated[Decimal, Field(max_digits=5, decimal_places=2)]` |
| `Enum` or `Uuid` | The Python type: the enum class, or `uuid.UUID` |
| `ARRAY(String(5))` | `list[Annotated[str, Field(max_length=5)]]` |
| Text without NUL, in a type that Restly does not check | An `AfterValidator`, as below |

```python
from typing import Annotated

from pydantic import AfterValidator

import fastapi_restly as fr


def no_nul(text: str) -> str:
    if "\x00" in text:
        raise ValueError("The text holds a NUL character.")
    return text


class NoteSchema(fr.IDSchema):
    text: Annotated[str, AfterValidator(no_nul)]
```
:::

### Database validation

A schema sees one request. It does not see other rows, the stored row or the
caller:

- A reference to another row: {class}`fr.MustExist
  <fastapi_restly.schemas.MustExist>`,
  {class}`fr.IDRef <fastapi_restly.schemas.IDRef>` and `fr.IDSchema[Model]`
  check that the row exists, and return 404 if it does not. See
  [Relationships](#reference-exists).
- A unique value, such as an email address: put a unique constraint on the
  column. The database rejects a second row with the same value, and Restly
  turns the error into a 409; see [Errors](#default-exception-handling).
- A rule that needs the database or the caller, such as "no new tasks in an
  archived project": check it in the `create` or `update` business method
  and raise an error there; see
  [Request Lifecycle & Hooks](#business-methods). The SaaS example checks
  such rules in
  [`app/tasks/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/tasks/views.py).

(utc-times)=
## Keep times in UTC

Give each time field the type
{data}`fr.UTCDatetime <fastapi_restly.schemas.UTCDatetime>`:

```python
class MeetingSchema(fr.IDSchema):
    name: str
    starts_at: fr.UTCDatetime
```

The field reads a time without an offset, such as `2024-01-01T09:30:00`, as
UTC. A time with an offset, such as `2024-01-01T10:30:00+01:00`, becomes the
same time in UTC. So `create` and `update` get each time in UTC. A response
shows it with `Z`, the offset of UTC, as JavaScript's `toISOString()` does: a
client that sends `"starts_at": "2024-01-01T10:30:00+01:00"` gets
`"starts_at": "2024-01-01T09:30:00Z"` back. A schema that Restly generates
gives each time column this type, and
{class}`fr.TimestampsSchemaMixin <fastapi_restly.schemas.TimestampsSchemaMixin>`
gives it to `created_at` and `updated_at`.

A filter value goes through the same type, so
`?starts_at__gte=2024-01-01T04:15:00-05:00` finds the meeting. In a URL,
write the `+` of an offset as `%2B`: a plain `+` means a space there.

Each column stores the time in UTC. A column without a time zone stores it
without the offset: `2024-01-01T09:30:00`. On a base with
{class}`fr.ModelDefaults <fastapi_restly.models.ModelDefaults>`, a
`Mapped[datetime]` column has a time zone. A column with
`mapped_column(DateTime())` has none, and neither has `Mapped[datetime]` on a
base without `fr.ModelDefaults`.

For local times, such as the start of a dinner booking in the time of the
restaurant, use a column without a time zone, and a field of type `datetime`
with a validator that converts the time. Restly then gets a time without an
offset, and keeps it as it is:

```python
from datetime import datetime
from typing import Annotated
from zoneinfo import ZoneInfo

import pydantic

AMSTERDAM = ZoneInfo("Europe/Amsterdam")


def amsterdam_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(AMSTERDAM).replace(tzinfo=None)


class BookingSchema(fr.IDSchema):
    starts_at: Annotated[datetime, pydantic.AfterValidator(amsterdam_time)]
```

A filter value goes through the field's validation too, also in the filter
of a [react-admin view](react_admin.md), so the filters convert it in the
same way.

:::{admonition} Details
:class: details

Without these rules, the database drivers decide, and they do not agree. For
a column without a time zone, asyncpg rejects a time with an offset, so the
request fails with a 500. For a column with a time zone, asyncpg reads a
time without an offset in the time zone of the app's Python process. psycopg
leaves both cases to PostgreSQL, which uses the time zone of the database
session. On an engine that Restly builds from a URL, that session is in UTC;
see [Engine defaults](#engine-defaults). SQLite drops the offset of every
time.

A field of type `datetime` keeps the time that the client sent. Restly still
changes the time to fit its column before the database sees it: in a filter,
and when it copies a request body onto the model, in
{func}`fr.objects.make_new_object <fastapi_restly.objects.make_new_object>`
and {func}`fr.objects.update_object <fastapi_restly.objects.update_object>`:

| Column | A time with an offset | A time without an offset |
|---|---|---|
| With a time zone | becomes the same time in UTC | gets the offset of UTC |
| Without a time zone | becomes the same time in UTC, without the offset | stays as it is |

So `authorize`, `create` and `update` get the time as the client sent it,
and a response shows a time from a column without a time zone without an
offset.

Restly changes a time to fit a column only when the model has a column with
the name of the field. A field without one, such as a `WriteOnly` field that
only `create` reads, gets UTC from its type only.

A filter method of your own, from
{func}`fr.list_filter <fastapi_restly.views.list_filter>`, gets its value as
the type of its parameter gives it. With `value: fr.UTCDatetime`, it gets the
time in UTC. Compare it with a column without a time zone as
`value.replace(tzinfo=None)`.

A time from the database has no offset when its column has no time zone, and
on SQLite always. A time from the schema has an offset. Python cannot order
such times: `<`, `>` and `-` raise `TypeError`. And `==` is always `False`,
and `!=` always `True`, without an error. Convert the time from the database
with {func}`fr.in_utc <fastapi_restly.schemas.in_utc>` first, as in
`data.ends_at > fr.in_utc(obj.starts_at)`. `fr.in_utc` follows the rule of
`fr.UTCDatetime`: a time without an offset is UTC, and a time with an offset
becomes the same time in UTC.

A column with a type of your own, a
{class}`~sqlalchemy.types.TypeDecorator`, gets a time with an offset as it
is, and so does a type with a variant for one database, from
`with_variant()`. Such a type may need the offset, as SQLAlchemy's recipe
that stores UTC in a column without a time zone does, and a variant may
store a time zone where the type itself does not. Restly only checks that
the time fits in UTC. A time without an offset gets the offset of UTC when
the type stores a time zone. SQLAlchemy's recipe stores none, and it refuses
a time without an offset, so give its column a field of type
`fr.UTCDatetime`, which gives every time the offset of UTC.

A time that is not between the years 1 and 9999 in UTC, such as
`9999-12-31T23:30:00-01:00`, gets 422 in a request body and 400 in a filter.

A `date`, and a time of day of type `time`, have no rule. A time of day has
no date, so summer time can change its time in UTC. A `time` column without
a time zone keeps the clock time of a value and drops its offset:
`09:00:00+02:00` becomes `09:00:00`.
:::

(nan-and-infinity)=
## Keep NaN and infinity

JSON has no number for NaN or infinity. So Restly writes them as the text
`"NaN"`, `"Infinity"` and `"-Infinity"`, in a response and in a JSON column. A
`float` field reads the text back as a number, so a client can send back the
values that it got:

```python
class Series(fr.IDMixin, Base):
    points: Mapped[list[float]] = mapped_column(
        fr.models.PydanticJSON(list[float])
    )
```

A client that sends `{"points": [1.5, "NaN", "Infinity"]}` gets
`{"id": 1, "points": [1.5, "NaN", "Infinity"]}` back, and the column stores
`[1.5, "NaN", "Infinity"]`. OpenAPI still shows a number for the field, so a
client must accept these three texts in a number field.

A Pydantic model of your own, such as the model of a
{class}`fr.models.PydanticJSON <fastapi_restly.models.PydanticJSON>` column,
follows its own setting: in a response, and for its fields of type `Any` in
a JSON column. Pydantic's default writes `null` there, which loses the
value. Give your models the setting that
{class}`fr.BaseSchema <fastapi_restly.schemas.BaseSchema>` has:

```python
import pydantic


class Point(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(ser_json_inf_nan="strings")

    lat: float
    lon: float
```

To give the setting to all of your models, put it in a base class of your
own.

Restly cannot keep the value in these cases:

- On SQLite, a `Float` column stores NaN as `NULL`, and a column that is not
  nullable refuses the row. PostgreSQL stores NaN and infinity as numbers.
- A plain `JSON` column gets the text only when Restly copies a request body
  onto the model. Your own code that writes the column goes through
  SQLAlchemy. SQLite then stores NaN as a number, and PostgreSQL refuses the
  row. In a part of type `Any`, a response shows such a number as `null`. For
  a free-form document that your own code writes, use
  `fr.models.PydanticJSON(dict[str, Any])`, which writes the text on each
  write.
- The text in a response needs FastAPI 0.130 or later, with its default
  response class. An older FastAPI, or `JSONResponse` as the response class,
  gives an error 500.

:::{admonition} Details
:class: details

A request body can hold the text, or `NaN` and `Infinity` without quotes, as
Python's `json` module writes them. A `float` field reads both. Send the
text: FastAPI cannot write an error message that holds `NaN` without quotes,
so such a body can get an error 500 instead of 422. A strict field, with
`strict=True`, refuses the text in a request.

Restly reads a row into the response schema in lax mode, also for a strict
class, as a `PydanticJSON` column reads its document. So a strict `float`
field reads the stored text back.

A part of type `Any`, such as a value in `dict[str, Any]`, reads the text back
as text, not as a number. So does a union with `str`, such as `float | str`.

For Pydantic's default in a response, set `ser_json_inf_nan="null"` in the
`model_config` of your schema.

PostgreSQL and MySQL refuse NaN as a number in a JSON column, because JSON has
no number for it. PostgreSQL's `to_jsonb('NaN'::float8)` gives the text
`"NaN"`, and Restly writes the same text.
:::

(field-aliases)=
## Use other field names in JSON

Use a normal Pydantic alias when the JSON name differs from the Python name:

```python
from pydantic import Field


class UserSchema(fr.IDSchema):
    first_name: str = Field(alias="firstName")
    email: str
```

A request body must use `firstName`. The responses use `firstName` too. The
Python name stays `first_name`, and Restly writes the value to the model
attribute with that name. List filters use the alias as well; see
[Filtering, Sorting & Pagination](filtering.md).

(own-response-schema)=
## Set your own response schema

By default, the response schema is the main schema without its `WriteOnly`
fields. Restly builds this class even when the schema has no `WriteOnly`
fields, so OpenAPI always shows `UserResponse`.

When what goes out differs more from what comes in, set
{attr}`response_schema <fastapi_restly.views.BaseRestView.response_schema>`
on the view. Everything that goes out follows it:

- every single response, and each item of the list response;
- the fields that the list filters and sort keys use, so a client can filter
  only on the fields that go out;
- the relationships that the view loads.

The create and update schemas still come from the main schema:

```python
class AccountSchema(fr.IDSchema):
    name: str
    email: str
    team_id: int | None = None


class AccountResponse(fr.IDSchema):
    name: str
    team: TeamSchema | None = None


class AccountView(fr.AsyncRestView):
    prefix = "/accounts"
    model = Account
    schema = AccountSchema
    response_schema = AccountResponse
```

`POST /accounts` takes `name`, `email` and `team_id`. Every response shows
`id`, `name` and the nested `team`, which the view loads. `?email=...` is not
a filter, because a client does not see `email`. Restly uses the class as you
wrote it: it does not remove `WriteOnly` fields from it.

To change how one object becomes a response, or to send another shape, see
[Responses](responses.md).

:::{admonition} Details
:class: details

Name the class `<Resource>Response`, as in `AccountResponse`. Restly names the
list response and the list params after it: `AccountListResponse` and
`AccountListParams`. For type checkers, put the class in the second type
parameter of the view, as in `fr.AsyncRestView[Account, AccountResponse]`; see
[Type Annotations](#view-generics).
:::

(generated-schema-names)=
## The names of the generated schemas

A schema that Restly generates is named `<Resource><Role>`. Resource is the
class name of the main schema without a final `Schema` or `Response`. For
the main schema `UserSchema`, the resource is `User`. So Restly generates
`UserResponse`, `UserListResponse`, `UserCreate`, `UserUpdate` and
`UserListParams`.

| Role | Class | View attribute |
|---|---|---|
| response | `UserResponse` | {attr}`response_schema <fastapi_restly.views.BaseRestView.response_schema>` |
| create schema | `UserCreate` | {attr}`create_schema <fastapi_restly.views.BaseRestView.create_schema>` |
| update schema | `UserUpdate` | {attr}`update_schema <fastapi_restly.views.BaseRestView.update_schema>` |
| list response | `UserListResponse` | none |
| list params | `UserListParams` | {attr}`list_params_schema <fastapi_restly.views.BaseRestView.list_params_schema>` |

A client sees these names in OpenAPI. It does not see the main schema,
unless another schema nests it or a custom route names it. The list params
are not a class in OpenAPI either: OpenAPI shows them as separate query
parameters.

The `404` for a row that does not exist names the resource too, such as
`User with id 5 was not found`. To change the text, override
{meth}`not_found <fastapi_restly.views.BaseRestView.not_found>`.

:::{admonition} Details
:class: details

The list response has no view attribute, because it has nothing of its own
to set. It is the envelope of the view's
{attr}`pagination <fastapi_restly.views.BaseRestView.pagination>`, filled with
the response schema. To change it, change one of those two; see
[Change the list envelope](#change-list-envelope). A react-admin view has no
list response schema: its list route returns a plain JSON list.

A view without a schema gets a generated one. For a model named `User` it is
`UserSchema`, so the names above stay the same. A schema with another name
keeps its full name as the resource: `UserRead` gives `UserReadCreate`.
:::

(name-your-schemas)=
## Name your schemas

OpenAPI keeps all classes in one list, by name. It has no modules. A
generated client uses these names as type names. So each class in your API
needs its own name, even when Python keeps two classes apart in different
modules.

Restly names the schemas that it generates after the main schema; see
[the names of the generated schemas](#generated-schema-names). Follow these
rules for the schemas that you write:

- Name the main schema after the resource that the client sees:
  `UserSchema`, or `AdminUserSchema` for an admin view of users. Two schemas
  named `UserSchema` in different modules clash.
- Use the names `UserResponse`, `UserCreate` and `UserUpdate` only for a
  view's response, create and update schemas, and set the class on the view:

  ```python
  class TaskCreate(fr.BaseSchema):
      title: str


  class TaskView(fr.AsyncRestView):
      prefix = "/tasks"
      model = Task
      schema = TaskSchema
      create_schema = TaskCreate
  ```

  A class for another purpose, such as the body of a bulk route, gets a name
  that says what it is: `TaskBulkItem`, `UserSummary`.
- Two views that share a schema also share the names of the classes that
  Restly generates. When the classes differ, for example because only one
  view is paginated, give one view a schema of its own:

  ```python
  class AllTasksSchema(TaskSchema):
      pass
  ```

  The list response takes its name from the response schema. So when the two
  views set the same `response_schema`, give one view a response schema of
  its own in the same way.

:::{admonition} Details
:class: details

Two different classes can still get the same name. For example, two views
share a schema but only one is paginated, or you wrote a `UserCreate` that is
not the same as the generated one. OpenAPI then shows both classes under long
names built from the module path, such as `app__users__views__UserCreate`.
When the module path is the same too, it numbers them, and the numbers follow
the order in which the views are registered. These names can change, and a
generated client then breaks.

When the app builds its OpenAPI spec, Restly checks for this and warns with a
{class}`RestlyDuplicateSchemaNameWarning <fastapi_restly.exc.RestlyDuplicateSchemaNameWarning>`
that names the classes and the views. Restly checks an app that you pass to
{func}`fr.configure(app, ...) <fastapi_restly.db.configure>` or
{func}`configure_tests(app=...) <fastapi_restly.testing.configure_tests>`, or
that you include a view on. Two classes with the same name are not a problem
when their JSON schemas are the same, docstrings and defaults included:
OpenAPI then shows them as one.
:::

(name-the-view-schemas)=
## Name the view's schemas in a custom route

A custom route can show the same types in OpenAPI as the CRUD routes. A route
decorator runs in the class body, before Restly builds the view's schemas.
So a custom route names them with markers from `fastapi_restly.views`. When
the view registers, Restly puts the view's own schema or type in place of each
marker:

| Marker | What it stands for | On `UserView` |
|---|---|---|
| {data}`ResponseSchema <fastapi_restly.views.ResponseSchema>` | {attr}`response_schema <fastapi_restly.views.BaseRestView.response_schema>` | `UserResponse` |
| {data}`ListResponseSchema <fastapi_restly.views.ListResponseSchema>` | {attr}`list_response_schema <fastapi_restly.views.BaseRestView.list_response_schema>` | `UserListResponse` |
| {data}`CreateSchema <fastapi_restly.views.CreateSchema>` | {attr}`create_schema <fastapi_restly.views.BaseRestView.create_schema>` | `UserCreate` |
| {data}`UpdateSchema <fastapi_restly.views.UpdateSchema>` | {attr}`update_schema <fastapi_restly.views.BaseRestView.update_schema>` | `UserUpdate` |
| {data}`IDType <fastapi_restly.views.IDType>` | {attr}`id_type <fastapi_restly.views.BaseRestView.id_type>` | `int` |

```python
from fastapi_restly.views import IDType, ListResponseSchema, ResponseSchema


@fr.include_view(app)
class UserView(AppView):
    prefix = "/users"
    model = User
    schema = UserSchema

    class Action(fr.Action):
        ACTIVATE = "activate"

    @fr.get("/inactive")
    async def inactive(self, list_params) -> ListResponseSchema:
        return await self.handle_get_many(list_params, where=User.active.is_(False))

    @fr.post("/{id}/activate", status_code=200)
    async def activate(self, id: IDType) -> ResponseSchema:
        user = await self.get_one(id)
        async with self.write_action(self.Action.ACTIVATE, obj=user):
            user.active = True
        return user
```

OpenAPI then shows `UserListResponse` and `UserResponse` for these routes,
as for the CRUD routes. A path parameter annotated `IDType` gets the path
converter of the id type, as the item routes do: here `/{id:int}`; see
[Item paths](#item-route-matching).

The routes return the list result and the row. Because they declare the
view's response, Restly passes these through
{meth}`to_list_response <fastapi_restly.views.BaseRestView.to_list_response>`
and
{meth}`to_single_response <fastapi_restly.views.BaseRestView.to_single_response>`,
as for the CRUD routes; see
[The response of a custom route](#custom-route-response).

A marker stands for the class of the view that registers. So a route on a
base view works for each view that subclasses it, with that view's schemas.
The `TrashView` of the SaaS example adds a trash list and a restore route to
two views this way, in
[`app/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/views.py).

:::{admonition} Details
:class: details

A marker works wherever a route can name a class: in a parameter annotation, in
the return annotation, in `response_model=`, in the `model` of `responses=`, in a
type alias, and inside another type, such as `list[ResponseSchema]` or
`ResponseSchema | None`. `Annotated[IDType, Path(ge=1)]`
keeps the `Path(ge=1)`. A path that gives a converter, such as `/{id:str}`,
keeps it. Registration raises for a type alias that names itself and holds a
marker, and for a marker in a field of a class.

For a type checker, each marker is `Any`, so it accepts a marker in any
annotation. A plain {class}`View <fastapi_restly.views.View>` has no model and
no schema, so a route on it cannot use the markers: registration raises
{class}`RestlyConfigurationError <fastapi_restly.exc.RestlyConfigurationError>`.

To look at the classes of a view, in a test or in a tool, read them on the
view, as in `UserView.response_schema`. Restly builds a class at the first read,
so this works before the view registers, once the classes that it needs exist.
Restly keeps this class, so set the settings of a view before the first read.

To use the classes where you have no view, build them with
{func}`derive_response_schema(schema) <fastapi_restly.schemas.derive_response_schema>`
and
{func}`derive_list_response_schema(response_schema, pagination=...) <fastapi_restly.schemas.derive_list_response_schema>`.
They return the same classes that the view uses, unless the view sets its own.
On a react-admin view, the list is `list[UserResponse]` instead. Pass the view's
pagination as it is, also when it is `None`. A list response with another envelope than
the view's has another shape, for example `data` without `total_count`.
There are then two classes named `UserListResponse`, and Restly warns; see
[Name your schemas](#name-your-schemas). A function builds these classes, so
type checkers do not accept them in an annotation, as in
`data: UserResponse`. At runtime they are normal classes, so add
`# type: ignore[valid-type]` on such a line.

A custom route that names `UserSchema` or `NumberedEnvelope[UserSchema]`
shows a type of its own in OpenAPI. Restly does not fill such a class from a
`ListResult`. So the route calls `to_list_response` itself.

A marker works in the annotation of a route, also inside a generic class, as in
`Data[ResponseSchema]`. It does not work in a field of a class that you write:
the field would have no type in OpenAPI, so registration raises. Make the class
generic, or [build it in a function](#schema-type).
:::

(schema-type)=
## Make dynamic schemas that depend on the view

Write a function when a schema differs for each view, in a way that you can
describe once on a base view. An example is a JSON key named after the resource.
{class}`SchemaType <fastapi_restly.views.SchemaType>` gives the function the
view class, and uses the type that the function returns. It goes where a class
goes: on a setting of the view, or in the annotation of a route. OpenAPI shows
the type of each view.

This base view gives each view its own keys in the list response. So
`GET /articles` sends `{"articles": [...], "articlesCount": 2}`:

```python
import pydantic

from fastapi_restly.views import SchemaType


def keyed_list(view):
    key = view.list_key
    return pydantic.create_model(
        f"{key.title()}List",
        data=(list[view.response_schema], pydantic.Field(alias=key)),
        total_count=(int, pydantic.Field(alias=f"{key}Count")),
    )


class ApiView(fr.AsyncRestView):
    list_response_schema = SchemaType(keyed_list)


@fr.include_view(app)
class ArticleView(ApiView):
    prefix = "/articles"
    model = Article
    schema = ArticleSchema
    list_key = "articles"
```

In a route, the function goes in an `Annotated` type. Here each view sets
`single_key`, so with `single_key = "article"`, `GET /articles/1` sends
`{"article": {...}}`:

```python
from typing import Annotated, Any

from fastapi_restly.views import IDType


def keyed_single(view):
    key = view.single_key
    return pydantic.create_model(
        f"{key.title()}Single",
        data=(view.response_schema, pydantic.Field(alias=key)),
    )


Single = Annotated[Any, SchemaType(keyed_single)]


class ApiView(fr.AsyncRestView):
    @fr.get("/{id}")
    async def get_one_endpoint(self, id: IDType) -> Single:
        obj = await self.handle_get_one(id)
        return {self.single_key: self.to_single_response(obj)}
```

:::{admonition} Details
:class: details

Six settings take a function: `schema`, `response_schema`, `create_schema`,
`update_schema`, `list_params_schema` and `list_response_schema`. The function
of the first five must return a Pydantic class, or registration raises. For
`list_response_schema`, see
[Use a different schema for the list route](#list-schema). A route can use any
type that FastAPI accepts.

A setting can also use `SchemaType` as a decorator. The name of the method is
the name of the setting:

```python
class ApiView(fr.AsyncRestView):
    @SchemaType
    def list_response_schema(view):
        return keyed_list(view)
```

Restly calls the function when something first reads the type, once for each
view class. It does not call the function when Python creates the class. So the
function can read any setting of the view, in any order, and it can use a class
that the module defines later. A function that reads its own result raises
{class}`RestlyConfigurationError <fastapi_restly.exc.RestlyConfigurationError>`,
and so does a function that returns `None`. A [marker](#name-the-view-schemas)
in what it returns gets the type of the view, as in `Data[ResponseSchema]`.

Restly keeps one result for each function. So define `Single` once, or use a
named function. Two lambdas are two functions, even when they look the same, so
they give two classes.

Give each class a name of its own, or OpenAPI shows long names and Restly warns;
see [Name your schemas](#name-your-schemas). A generic class gets a name from
its type argument, such as `Data_UserResponse_`. For another class, use
`pydantic.create_model(name, ...)`, as above. A class that you rename later, with
`__name__`, keeps its first name in OpenAPI.

A setting holds a function or a class. It cannot hold a
[marker](#name-the-view-schemas), because Restly fills a marker in a route. So
`list_response_schema = list[ResponseSchema]` raises at registration. Write
`list_response_schema = SchemaType(lambda view: list[view.response_schema])`.
In this lambda, mypy reports `name-defined`, so add `# type:
ignore[name-defined]`, or write a `def` with `view: Any`.

A subclass that sets a class on the setting uses that class. The function stays
for the other subclasses. A subclass with a new `schema` gets the function
back; see [When a subclass view gets new classes](#subclass-new-classes).
:::

(json-document-columns)=
## Store a Pydantic model in a JSON column

Annotate a column with a Pydantic model. On a base with
{class}`fr.ModelDefaults <fastapi_restly.models.ModelDefaults>`, the column is
then a {class}`fr.models.PydanticJSON <fastapi_restly.models.PydanticJSON>`
column. It stores the model as a JSON document, and it loads the document as
the model:

```python
import pydantic


class Address(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(frozen=True)

    street: str
    city: str


class Shop(fr.IDMixin, Base):
    name: Mapped[str]
    address: Mapped[Address]
    branches: Mapped[dict[str, Address]] = mapped_column(
        fr.models.PydanticJSON(dict[str, Address])
    )
```

The generated schema has the field `address: Address`. So a request with an
address that does not fit returns 422, and a response shows the address as a
JSON object. In your own code, `shop.address` is an `Address`. A type that is
not one model, such as `dict[str, Address]`, needs `mapped_column()`, as
`branches` shows. On PostgreSQL, use
{class}`fr.models.PydanticJSONB <fastapi_restly.models.PydanticJSONB>` for a
`JSONB` column. Alembic needs a hook in `env.py` for these columns; see
[Write a PydanticJSON column into a migration](#pydantic-json-migration).

As with SQLAlchemy's `JSON` type, a change inside the document is not saved.
`shop.address.city = "Utrecht"` changes the object, but not the row. Assign a
new value:

```python
shop.address = shop.address.model_copy(update={"city": "Utrecht"})
```

With `frozen=True`, as in the example, the mistake fails: the assignment to
`shop.address.city` raises a `ValidationError`. `frozen` does not reach into a
list in the model, so use a tuple, such as `tuple[str, ...]`, for a list. A
dict at the top level, as in `branches`, is not frozen either. Wrap its type
in SQLAlchemy's {class}`~sqlalchemy.ext.mutable.MutableDict`. Then the
session saves a change such as `shop.branches["south"] = Address(...)`:

```python
from sqlalchemy.ext.mutable import MutableDict

branches: Mapped[dict[str, Address]] = mapped_column(
    MutableDict.as_mutable(fr.models.PydanticJSON(dict[str, Address]))
)
```

The column validates the document each time it loads a row, also outside a
view. So when you change the model, a stored document that no longer fits it
makes each load of its row fail. A new field with a default is safe. For
other changes, update the stored documents in a migration.

Use a `PydanticJSON` column for a document with a fixed shape. For a
free-form document, use a plain `JSON` column, as in
`Mapped[dict[str, Any]] = mapped_column(JSON)`. For a document with a secret,
write a SQLAlchemy {class}`~sqlalchemy.types.TypeDecorator` of your own,
because the JSON dump masks the secret. Keep a value that the server sets,
such as the time it checked an address, in a column of its own. A request
writes the whole document, so it would replace such a value.

:::{admonition} Details
:class: details

**Writes and reads.** The column validates a value when it writes it. So a
dict works too, and so does another model with the same fields, such as a
schema class of your view. A value that does not fit fails at the flush.
Then the column dumps the value as `TypeAdapter(Address)` does in JSON mode,
with `round_trip=True`. A `datetime`, a `UUID` or a `Decimal` becomes text,
and a set becomes a list. A NaN or an infinity becomes text; see
[Keep NaN and infinity](#nan-and-infinity). A serializer in the type, such as
a `PlainSerializer`, runs, and the column stores what it returns. A subclass of
`Address` is stored as an `Address`, without its own fields, as a field of
type `Address` is in `model_dump()`. The keys are the names of the fields,
not their aliases, also with `serialize_by_alias=True`. A response reads the
document by the names, as it reads the columns. A computed field is not
stored, so a model with `extra="forbid"` accepts the document. A `Json[T]`
field is stored as text, as its validation reads it. A load reads the stored
document in lax mode, also for a strict model. So it reads a `datetime` from
text, a tuple from a list, and a `float` from `"NaN"`.

**Refused types.** Some types raise
{class}`RestlyConfigurationError <fastapi_restly.exc.RestlyConfigurationError>`
when you define the column, because the dump would not store all of the
value:

- A {data}`WriteOnly <fastapi_restly.schemas.WriteOnly>`,
  `Field(exclude=True)` or `Field(exclude_if=...)` field, also in a nested
  model or a `TypedDict`. The dump leaves the field out.
- A field of Pydantic's secret types, such as `SecretStr`, also in a nested
  model, a `TypedDict` or a `NamedTuple`. The dump masks it, so the column
  would store `**********`.
- A dataclass, and an iterator type, such as `Iterable`.

A part typed `Any` is checked when you write it. For such documents, write a
`TypeDecorator` that stores the document as you need. Its bind processor gets
the Pydantic model itself, not a dump. You can still mark the whole column
`WriteOnly`. That hides the column in responses, and the stored document
keeps all its fields.

**`ReadOnly` fields.** A {data}`ReadOnly <fastapi_restly.schemas.ReadOnly>`
field raises too, also in a nested model, a `TypedDict` or a `NamedTuple`. A
request writes the whole document. So a `PATCH` that leaves out the field, as
OpenAPI asks, would set it back to its default, and the value that the server
set would be lost. Keep such a value in a column of its own. You can still
mark the whole column `ReadOnly`. The `id` of a reference, such as
`fr.IDRef[User]`, does not count, because the client sends it. So a view
schema, with its read-only `id`, cannot be the type of a document. Use a
model of its own for the document.

**The same as `JSON`.** `None` is stored as JSON `null`, and
`fr.models.PydanticJSON(Address, none_as_null=True)` stores SQL `NULL`. A query
reads a part of the document as with `JSON`, as in
`Shop.address["city"].as_string() == "Utrecht"`. A dict or a model in a
query is dumped, but not validated, because it can be a part of a document.

**`JSONB` for each model.** The `type_annotation_map` of `fr.ModelDefaults`
has `pydantic.BaseModel: fr.models.PydanticJSON(pydantic.BaseModel)`, as
SQLAlchemy maps an enum with `enum.Enum: Enum(enum.Enum)`. Put
`pydantic.BaseModel: fr.models.PydanticJSONB(pydantic.BaseModel)` in the map
of your base, and each model gets a `JSONB` column. Your own entry wins; see
[Change a default](#change-model-defaults).

**A plain `JSON` column with a schema of your own.** A
`Mapped[dict[str, Any]]` column gets the field type `dict[str, Any]` in a
generated schema, which does not check the document. To check it only in the
view, write the schema yourself and give the field a Pydantic model as its
type, such as `Address` or `dict[str, Address]`. Restly dumps the value
before it assigns it to the column. Then it refuses the same fields, in each
model in the value. It cannot check the keys of a `TypedDict` or the fields
of a `NamedTuple`, because by then the value is a plain dict or tuple. The
column has no type of its own, so this dump goes by
the type of the value: a subclass keeps its own fields, and a serializer of
the field type, such as a `PlainSerializer`, does not run. A serializer in a
model does. A NaN or an infinity becomes text, as in a `PydanticJSON`
column; see [Keep NaN and infinity](#nan-and-infinity). The model attribute
then holds a dict, not a model. A `PydanticJSON` column says the shape once,
on the model, and holds the model in all of your code.
:::

## Internals

This section explains how Restly builds the classes. You do not need it to
use them.

### How the markers work

`ReadOnly` and `WriteOnly` are `typing.Annotated` aliases. Each one adds a
marker object and a Pydantic `Field` to the type:

- `ReadOnly[T]` is `T` with the read-only marker and a `readOnly` flag for
  the JSON schema.
- `WriteOnly[T]` is `T` with the write-only marker, a `writeOnly` flag for the
  JSON schema, and `exclude=True`. Pydantic's `exclude` removes the field in
  every serialization of the schema. A value can only leak through a response
  that never passes through the schema, such as a raw dict or ORM object that
  a route returns without a `response_model`.

Restly finds a marker in the field's top-level metadata. That is why a marker
inside a union or a container is rejected. `BaseSchema` checks this when the
class is defined. A view checks its own classes again when it registers, which
also covers classes that do not derive from `BaseSchema`.

### How Restly builds the response, create and update schemas

Restly builds each schema the first time something reads it, once for each view.
For the main schema `UserSchema`:

- {attr}`response_schema <fastapi_restly.views.BaseRestView.response_schema>`
  comes from
  {func}`derive_response_schema() <fastapi_restly.schemas.derive_response_schema>`.
  It is a subclass of `UserSchema` that deletes the `WriteOnly` fields. Each
  call with the same schema returns the same class, so a custom route can
  name it.
- {attr}`create_schema <fastapi_restly.views.BaseRestView.create_schema>` comes
  from {func}`derive_create_schema() <fastapi_restly.schemas.derive_create_schema>`.
  It is a subclass of `UserSchema` with `OmitReadOnlyMixin` first in the MRO.
  That mixin deletes the `ReadOnly` fields from `cls.model_fields` and rebuilds
  the class with `model_rebuild(force=True)`.
- {attr}`update_schema <fastapi_restly.views.BaseRestView.update_schema>` comes
  from {func}`derive_update_schema() <fastapi_restly.schemas.derive_update_schema>`.
  It adds `PatchMixin` as well. After the `ReadOnly` fields are gone, it sets the
  default of every field to `None`, so the client may leave out each field.
  The defaults of `UserSchema` are **replaced** by `None`, not kept. Each
  field keeps its type, and Pydantic does not validate the `None` default,
  also when the class sets `validate_default=True` or a validator sets
  `always=True`. A field that the client does not send is not written:
  Restly writes only the fields in `model_fields_set`. In OpenAPI, no field
  of the update schema is required.

The create and update schemas also leave out a primary key that the server
generates, even when the schema does not mark it `ReadOnly`. Generated means an
autoincrement column, a column with a default, or a dataclass field outside
`__init__`. A primary key without any of these is a natural key, and the client
sends it.

These schemas are subclasses, so they keep the validators and serializers of
`UserSchema`. When a schema deletes a field, it keeps the validators and
serializers of that field too, and Pydantic does not run them there. So a
validator for a `ReadOnly` field, a `WriteOnly` field or a generated primary
key does not need `check_fields=False`.

A subclass of one of these schemas keeps the fields out that the schema deleted,
unless the subclass declares such a field itself. So `class MyUpdate(UserUpdate)`
cannot write a `ReadOnly` field by accident.

Declare `response_schema`, `create_schema` or `update_schema` on the view class
to use your own class instead.

(subclass-new-classes)=
### When a subclass view gets new classes

A subclass inherits the classes that its base view declares. Restly builds a
class again for the subclass in two cases: the base view does not declare it, or
what it is built from changes. A subclass that declares a new `schema` gets a new
`response_schema`, `create_schema`, `update_schema`, `list_params_schema` and
`list_response_schema`. A subclass that declares a new `response_schema` gets new
`list_params_schema` and `list_response_schema`. A function counts as a new
class here. A view without a schema builds it from `model`, so there a new `model` counts as a
new `schema`. A class that Restly generates for the subclass is not a change of
its own, so it does not replace a class that the base declares.

The list params are also built from `pagination`, the list settings such as
`include_filters` and `sort_query_param`, and the filter methods. A subclass
that changes one of them gets new `list_params_schema` when the base declares
the list params that Restly would generate for it, made with
{func}`derive_list_params_schema() <fastapi_restly.query.derive_list_params_schema>`.
Other declared list params stay, such as ones from a narrower schema. A
subclass with other settings then fails when it registers, and the message
says what to change. List params that you write by hand take no list
settings and no filter methods. A new `schema` replaces them too, so a
subclass with a new schema declares its list params again.

(auto-generated-schemas)=
### How Restly generates a schema

{func}`derive_schema(model, ...) <fastapi_restly.schemas.derive_schema>`
builds the schema of a view without one. It makes one field for each column
on the model's SQLAlchemy mapper, inherited columns included. A column's type
comes from its `Mapped[T]` annotation, or from the plain annotation on a
SQLModel table. Under `from __future__ import annotations`, Restly evaluates
the annotation string in the model's module. When no annotation resolves, the
column type's `python_type` is used. A column with neither raises `TypeError`
that names the attribute. Synonyms and composites get no field of their own:
the columns behind them already have one. A field allows `null` (`None` in
Python) only when its column is nullable. `mapped_column(nullable=...)`
decides this, as it does for SQLAlchemy, even when the `Mapped[...]`
annotation says otherwise.

The create schema follows the model's constructor, as SQLAlchemy does. The
client may leave out a field when its column is nullable, or when the
constructor does not require it and the model then still gives the column a
value. That value comes from the column, such as `insert_default` or
`server_default`, or from the constructor: a `default` other than `None`, or
a `default_factory`. Restly calls the constructor with the fields that the
client sent, so that value applies.

On a dataclass model, a column with only an `insert_default` or a
`server_default` is a required argument of the constructor, so its field is
required. To let the client leave it out, add `default=None`, as SQLAlchemy
does for a value that the database fills:
`mapped_column(server_default="x", default=None)`. On SQLAlchemy 2.0, a JSON
column stores that `None` as JSON `null` instead of its default, so Restly
keeps such a field required. Use `default_factory=list`, or
`JSON(none_as_null=True)`, for a JSON column. For a column that the client
must never write, such as a `Computed` column, use `init=False`, as
SQLAlchemy does. The field is then `ReadOnly`.

Three rules decide the rest:

- **Base classes**: Restly checks whether the model has fields *named*
  `id`, `created_at` and `updated_at`, and picks
  {class}`IDSchema <fastapi_restly.schemas.IDSchema>`,
  {class}`TimestampsSchemaMixin <fastapi_restly.schemas.TimestampsSchemaMixin>`
  and {class}`BaseSchema <fastapi_restly.schemas.BaseSchema>` from that. It
  does **not** look at the model's Python base classes. A model with a field
  named `id` for another reason also gets `IDSchema`.
- **ReadOnly fields**: the fields `id`, `created_at` and `updated_at` are
  always `ReadOnly`. So is a `column_property` over a SQL expression, which
  cannot be written. So is a column that the constructor does not take
  (`init=False`), such as a tenant column with an `insert_default`: the model
  or the server sets it, so the client must not. To let the client write such
  a column, remove `init=False`, so that the constructor takes it. Any other
  column that the server fills, for example with a server default, is
  **not** `ReadOnly`.
- **Relationships**: never included. A foreign key column, such as
  `customer_id`, is an ordinary field. With `init=False`, it is `ReadOnly`,
  as the rule above says. To write a relationship, or to show it in a
  response, write the schema yourself: `customer: fr.IDRef[Customer]` for
  its id, or a nested schema for the whole row. See
  [Choose a reference style](#reference-styles).

A view and a direct call of `derive_schema(model)` give the same schema.

### Column types and field types

`convert_sqlalchemy_type_to_pydantic` maps the Python type of each column,
from its annotation or its column type, to a Pydantic field type, as in this
table. A type keeps its name, except a time: it gets
[`fr.UTCDatetime`](#utc-times).

| SQLAlchemy / Python annotation | Pydantic field type |
|-------------------------------|---------------------|
| `str` | `str` |
| `int` | `int` |
| `float` | `float` |
| `bool` | `bool` |
| `datetime` | `fr.UTCDatetime` |
| `date` | `date` |
| `time` | `time` |
| `timedelta` | `timedelta` |
| `UUID` | `UUID` |
| `Decimal` | `Decimal` |
| `dict` / `dict[str, Any]` | `dict` / `dict[str, Any]` |
| `list` / `list[T]` | `list` / `list[T]` |
| `enum.Enum` subclass | same enum subclass |
| Pydantic model, such as `Address` | same model |
| `fr.models.PydanticJSON(T)` column without an annotation | `T` |
| SQLAlchemy `Text`, `String` | `str` |
| SQLAlchemy `Integer` | `int` |
| SQLAlchemy `Float` | `float` |
| SQLAlchemy `Boolean` | `bool` |
| SQLAlchemy `DateTime` | `fr.UTCDatetime` |
| SQLAlchemy `Date` | `date` |
| SQLAlchemy `Time` | `time` |
| SQLAlchemy `Interval` | `timedelta` |

`Any` and other generic types, such as `tuple[int, int]`, also stay as they
are, and so does a union, such as `Cat | Dog`. Any other type raises
`TypeError` when Restly generates the schema. For a custom column type, write
the schema yourself.

## See also

- [Relationships](relationships.md): foreign keys and
  relationships as ids or nested objects, and the check that a referenced row
  exists.
- [Responses](responses.md): how one object becomes a response,
  envelopes, and [a different schema for the list route](#list-schema).
- [Filtering, Sorting & Pagination](filtering.md): the filters and sort
  keys that come from the response schema.
- [Models](models.md): values that the server sets on every write path.
