Database API
============

``fastapi_restly.db`` implements the connection layer: ``configure()`` for
process-wide setup, session context managers and FastAPI session dependencies,
engine accessors, table creation helpers for development, and the
savepoint-only mode used in testing.

.. py:data:: fastapi_restly.db.AsyncSessionDep

   ``Annotated`` alias that supplies a SQLAlchemy ``AsyncSession`` through
   FastAPI dependency injection. The session uses the configuration set by
   :func:`~fastapi_restly.db.configure`.

.. py:data:: fastapi_restly.db.SessionDep

   ``Annotated`` alias that supplies a SQLAlchemy ``Session`` through FastAPI
   dependency injection. The session uses the configuration set by
   :func:`~fastapi_restly.db.configure`.

.. automodule:: fastapi_restly.db
   :members:
   :undoc-members:
   :show-inheritance:

.. seealso::

   :doc:`/howto_existing_project` shows how to wire Restly into existing
   engines and sessions, and :doc:`/deploying` covers production engine
   configuration.
