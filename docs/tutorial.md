# Getting Started with FastAPI-Alchemy

## Installation

Start with creating a [virtual environment](https://fastapi.tiangolo.com/virtual-environments/#activate-the-virtual-environment).

Then install `fastapi-ding` using `pip`.

```bash
$ pip install fastapi-ding
```
Although Ding aims to minimize dependencies, it is built on top of FastAPI and SQLAlchemy so it necessarily comes with quite a list of installed packages.

## Setting Up New FastAPI-Alchemy Project

If you are already working on a FastAPI project and want to know how you can include Ding see [Using Ding in an Existing Project](existing.md).

The simplest possible FastAPI-Alchemy project could look like this:
```python
from fastapi_alchemy import DingBase, AsyncAlchemyView
from sqlalchemy import Mapped

class World(DingBase):
    message: Mapped[str]

class WorldView(AsyncAlchemyView):
    prefix = "world"
    model = World
```



