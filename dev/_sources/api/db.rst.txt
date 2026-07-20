Database API
============

``fastapi_restly.db`` implements the connection layer: ``configure()`` for
process-wide setup, session context managers and FastAPI session dependencies,
engine accessors, table creation helpers for development, and the
savepoint-only mode used in testing.

.. automodule:: fastapi_restly.db
   :members:
   :undoc-members:
   :show-inheritance:

.. seealso::

   :doc:`/howto_existing_project` shows how to wire Restly into existing
   engines and sessions, and :doc:`/deploying` covers production engine
   configuration.
