You've got your CRUD endpoints, what more could you possibly want? Oh the boss wants to download an XML file generated from your Elasticsearch index, but with all the key-values reversed?

```python
from fastapi_alchemy import AsyncAlchemyView, include_view

@include_view(app)
class MyView(SQLAlchemy):

    @view_route()
    def disappointment(self):
        
```
