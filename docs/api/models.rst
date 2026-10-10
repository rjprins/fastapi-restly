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

``fastapi_restly.models`` also provides two cascade strings for
``relationship(cascade=...)`` on async sessions. ``CASCADE_ALL_ASYNC`` is
``"save-update, merge, delete, expunge"``, and
``CASCADE_ALL_DELETE_ORPHAN_ASYNC`` adds ``"delete-orphan"``. SQLAlchemy's
``"all"`` also includes ``"refresh-expire"``: then a plain
``session.refresh(obj)`` expires the related objects, and they lazy-load on the
next access, which fails on an async session. Restly's own ``save_object``
refreshes by attribute name and does not cascade, so ``"all"`` is safe on
Restly's write paths. Use these constants where your own code refreshes an
object on an async session.

.. seealso::

   :doc:`/getting_started` discusses choosing between your own
   DeclarativeBase and the convenience bases.
