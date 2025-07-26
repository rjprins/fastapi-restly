"""
Query Modifiers v2 Example

This example demonstrates the new query modifiers v2 functionality.
"""

import fastapi_ding as fd
from fastapi import FastAPI
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime

# Setup database
fd.setup_async_database_connection("sqlite+aiosqlite:///example_v2.db")

app = FastAPI()

# Define a model
class User(fd.IDBase, fd.TimestampsMixin):
    name: Mapped[str] = mapped_column(unique=True)
    email: Mapped[str] = mapped_column(unique=True)
    age: Mapped[int]
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


# Configure the framework to use v2 query modifiers
fd.set_query_modifier_version(fd.QueryModifierVersion.V2)

# Create view with v2 query modifiers
@fd.include_view(app)
class UserView(fd.AsyncAlchemyView):
    prefix = "/users"
    model = User


# Example usage and comparison
if __name__ == "__main__":
    import uvicorn
    
    print("=== Query Modifiers V2 Example ===")
    print()
    print("V2 Interface (Standard HTTP):")
    print("  Pagination: ?page=2&page_size=25")
    print("  Sorting: ?order_by=name,-age")
    print("  Filtering: ?name=John&age__gte=25&is_active__isnull=false")
    print("  Range filters: ?age__gt=18&age__lte=65")
    print("  Null filters: ?email__isnull=true")
    print()
    print("V1 Interface (JSONAPI-style):")
    print("  Pagination: ?limit=25&offset=25")
    print("  Sorting: ?sort=name,-age")
    print("  Filtering: ?filter[name]=John&filter[age]=>=25&filter[is_active]=!null")
    print()
    print("Key differences:")
    print("  - V2 uses direct field names instead of filter[field]")
    print("  - V2 uses __suffixes for operators (__gte, __lte, __isnull)")
    print("  - V2 uses page/page_size instead of limit/offset")
    print("  - V2 uses order_by instead of sort")
    print()
    print("Starting server...")
    print("Try these URLs:")
    print("  GET /users?page=1&page_size=10")
    print("  GET /users?order_by=name,-age")
    print("  GET /users?name=John&age__gte=25")
    print("  GET /users?is_active__isnull=false")
    
    uvicorn.run(app, host="0.0.0.0", port=8000) 