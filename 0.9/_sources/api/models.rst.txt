Models API
==========

``fastapi_restly.models`` provides the SQLAlchemy declarative base classes and
mixins (``DataclassBase``, ``IDBase``, ``IDMixin``, ``TimestampsMixin``) that
Restly models are declared against.

.. automodule:: fastapi_restly.models
   :members:
   :undoc-members:
   :show-inheritance:
   :exclude-members: utc_now, underscore, metadata, CASCADE_ALL_ASYNC, CASCADE_ALL_DELETE_ORPHAN_ASYNC, TableNameMixin

.. seealso::

   :doc:`/getting_started` discusses choosing between your own
   DeclarativeBase and the convenience bases.
