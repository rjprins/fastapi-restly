Utils API
=========

``fastapi_restly.utils`` holds helpers that belong to no single Restly
subsystem. Today that is the lazy proxy, which defers building an object until
something reads it.

.. automodule:: fastapi_restly.utils
   :members:
   :undoc-members:
   :show-inheritance:

.. seealso::

   :doc:`/howto_project_structure` covers where settings live in an
   application, and why importing your ``main`` module must not require a
   configured environment.
