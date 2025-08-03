# The TODO List
 - Begin een website met korte artikeltjes over FastAPI-Ding
 - Begin een newsletter (to receive a notification for the first release!)
 - Update update_object and make_new_object to respect ReadOnly (they already do this?)
 - Remove make_response_schema
 - Create make_response function that respects WriteOnly
 - Add WriteOnly annotation
 - Create nice operationIds in openapi
 - Skip creation/update schema creation!??!?
 - Aliases are still not being handled correctly in the case of nested schemas. See warehouseoption schema.
 - validators are not copied / inherited correctly in schema creation.
 - Refactor tests to re-use more code
 - Change PUT to PATCH and just forget about PUT altogether
 - Document process_* functions intended for overriding
 - Add self.meta for meta response data. For example, warn about ignored fields on POST and PATCH. Use self.meta for "out-of-channel" communication to make_response()
 - Read up on typing libraries correctly: https://typing.python.org/en/latest/guides/libraries.html


# DONE
 - Simplify create_ schema functions to just subclass and rename.
 - Add json_schema_extra={"readOnly": True} and writeonly to pydantic fields.. during view init



# Where I am at right now

I want to get this out of the door ASAP.
Please cut corners as much as you can.
We want it LIVE LIVE LIVE.

MAIN GOAL: Get the more complicated example project working.
Secondary goal: Get the docs up to a minimal state to get anyone started.

Which functionality can I *skip*?

What is go live?

## 1. It is on GitHub ✅
If it is public on GitHub it is open source™
Doesn't have to be functional yet, so we can do this today!

## 2. Package on PYPI
Now it will for real. Minimal requirements for this:
* *NO NEW FUNCTIONALITY NEEDED*
* Tests have enough coverage
* Naming is sort-of stable
* Minimal set of docs

- [ ] Run test suite 
- [ ] Run test suite on py 3.10 - 3.13

Start with version 0.5


### Things to test before publishing:

* Async shop example fully functional?
  * Test every endpoint
* Sync shop example fully functional?
  * Test every endpoint
* Blog example fully functional?
  * Test every endpoint
* Test testing helpers both sync and async
  
Do I want to support "No alembic/just create tables?"

## TODO
* Find a nicer way to insert extra dependencies per endpoint (for permissions on post, put, delete, for example) other than overriding and copying the route details
* Improve route() decorator: Have get, post, put, delete variants and full keyword definitions?
* Change index query parameters - make it simpler (foo=1, instead of filter[foo]=1)
* Handle mixing query_params and other query parameters
* Handle inheritance of validators correctly when creating schemas!
* Look critically at Settings vs FA_Globabls
* Split common functions from AsyncAlchemyView and AlchemyView into base class
* Document pytest_fixtures and how shared sync_connection works between session and async_session
  as well as document savepoint mechanics and limitations.
* Do multi python versions tests. From 3.10 upwards.
* Go over naming - Did I get the naming of things spot on?
  - make_session? Maybe should be Session? ✅ (Changed name of proxies)
  - Base => Base ? ORMBase and orm_cls everywhere
  - self.db => self.session ? ✅
  - any 'model' references rename to schema
  - All modules should start with underscore except __init__.py ✅
* Update the docs
  - Start working on API docs
  - Make clear getting started / tutorial
* Make nested views work nicely (concate route prefixes)
* Address overriding type warnings for index, get, post etc
  - By using a very generic base method that does nothing? e.g. get(*args, **kwargs)?
* Auto-create pydantic model from sqlalchemy model
* Improve query modifiers
  - Maybe not use filter[field]= but just field= style?
* Add metadata to index response
  - total_count
  - filter options
  - page count?
* Wrap all responses in {"data": {}} ?
* Create a nice tests suite that tests the example projects
* Add alembic tests by default




