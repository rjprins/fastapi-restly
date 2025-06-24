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
  
Do I want to support "No alembic/just create tables?"

## Future to-dos (after go-live)

* Do multi python versions tests. From 3.10 upwards.
* Go over naming - Did I get the naming of things spot on?
  - make_session? Maybe should be Session?
* Update the docs
  - Start working on API docs
  - Make clear getting started / tutorial
* Make nested views work nicely (concate route prefixes)
* Auto-create pydantic model from sqlalchemy model
* Improve query modifiers
  - Maybe not use filter[field]= but just field= style?
* Add metadata to index response
  - total_count
  - filter options
  - page count?
* Wrap all responses in {"data": {}} ?
* Create a nice tests suite that tests the example projects




