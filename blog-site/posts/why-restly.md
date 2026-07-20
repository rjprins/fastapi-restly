---
blogpost: true
date: 2026-07-20
category: meta
---

# Why I made FastAPI-Restly

Every serious web application I have worked on needed the same layer: a REST
API over a relational database. And on every FastAPI project, I found myself
writing that layer again. Five routes per resource. Three Pydantic schemas per
model, nearly identical, drifting apart over time. The same session dependency
declared on every endpoint. A filter grammar for list endpoints, pagination,
sorting, and the test fixtures to cover it all. None of it is hard. All of it
is work, and it multiplies by the number of resources in the application.

FastAPI is excellent, and its restraint is part of why: it owns routing,
validation, and dependency injection, and deliberately stops there. It has no
opinion about your database or about how a resource maps onto routes. For a
framework, that is the right call. For an application team, it means someone
rebuilds the resource layer on every project, slightly differently each time.

Class-based views are an old and good answer to this. Django developers will
recognize the shape immediately: declare what the resource is, inherit the
mechanics, override where your domain disagrees. I wanted that shape without
leaving the stack I had chosen, because the stack was never the problem.

So the design principle of FastAPI-Restly fits in four lines:

> SQLAlchemy stays SQLAlchemy.
> Pydantic stays Pydantic.
> FastAPI stays FastAPI.
> Restly owns the repetitive REST resource layer.

Declare the resource, and the boring parts are generated:

```python
import fastapi_restly as fr

@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
```

That view serves list, create, read, update, and delete, with schemas derived
from the model, filtering and pagination on the list endpoint, and a session
whose commit the framework owns. When your domain disagrees with a default,
you override one seam instead of forking the mechanics: the business method
for domain logic, the handler for the write bracket, or the endpoint method
when the HTTP contract itself has to change.

There is a little framework magic inside, and it is contained on purpose.
FastAPI is function-first, so making real classes feel native means rewriting
endpoint signatures and binding dependencies as instance attributes. That
happens in one place, behind boring names, so application code stays explicit
where it matters.

Restly did not start as an open source project. The first version grew inside
[WHERE: the applications I built at ...], and for four years it stayed
internal, reshaped every time real usage disagreed with the design. [WHY NOW:
what made you take it public?] The version that is public today is the third
redesign of its extension seams, not a weekend extraction.

It is public now because I want it to survive contact with codebases that are
not mine. The API is settling on the way to 1.0, which means breaking changes
are still allowed where they make the design better. If you want to try it,
[Getting Started](https://www.fastapi-restly.org/getting_started.html) is the
fast path, and issues are welcome, including the "why is this like this" kind.
