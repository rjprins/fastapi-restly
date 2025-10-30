# Custom Endpoints

You can add custom endpoints to your views:

```python
from fastapi_restly import AsyncAlchemyView, include_view

@include_view(app)
class UserView(AsyncAlchemyView):
    prefix = "/users"
    model = User
    schema = UserSchema

    @view_route()
    def custom_endpoint(self):
        return {"message": "This is a custom endpoint"}
```

You've got your CRUD endpoints, what more could you possibly want? Oh the boss wants to download an XML file generated from your Elasticsearch index, but with all the key-values reversed?

```python
from fastapi_restly import AsyncAlchemyView, include_view

@include_view(app)
class MyView(SQLAlchemy):

    @view_route()
    def disappointment(self):
        
```
