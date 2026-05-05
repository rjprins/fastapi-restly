Testing API
===========

Install the optional testing dependencies before importing this module::

    pip install "fastapi-restly[testing]"

The testing extra installs a pytest plugin entry point, so pytest auto-loads the
fixtures. If your project disables plugin autoloading, add the following line to
your ``conftest.py``::

    pytest_plugins = ["fastapi_restly.pytest_fixtures"]

This imports the namespaced fixtures (``restly_app``, ``restly_client``,
``restly_session``, ``restly_async_session``, etc.) into your test session
without needing to import them individually.

.. automodule:: fastapi_restly.testing
   :members:
   :undoc-members:
   :show-inheritance:
