Testing API
===========

To use the fixtures provided by this module, add the following line to your
``conftest.py``::

    pytest_plugins = ["fastapi_restly.pytest_fixtures"]

This imports all fixtures (``app``, ``client``, ``session``, ``async_session``,
etc.) into your test session without needing to import them individually.

.. automodule:: fastapi_restly.testing
   :members:
   :undoc-members:
   :show-inheritance:
