Models API
==========

``fastapi_restly.models`` provides optional convenience bases and mixins
(``DataclassBase``, ``IDBase``, ``IDMixin``, ``TimestampsMixin``). They are not
required: Restly also accepts models declared against your own SQLAlchemy
``DeclarativeBase``.

Which base should I use?
------------------------

* Use ``IDBase`` for a typical dataclass model with an auto-incrementing
  integer primary key.
* Use ``DataclassBase`` when you want the same dataclass semantics, automatic
  table naming, and ``awaitable_attrs``, but need to define your own primary
  key.
* Keep your own SQLAlchemy ``DeclarativeBase`` for existing model layers or
  standard, non-dataclass constructor semantics.
* Add ``TimestampsMixin`` when a dataclass model needs ``created_at`` and
  ``updated_at`` columns. ``IDMixin`` is the lower-level integer-primary-key
  building block used by ``IDBase``.

.. automodule:: fastapi_restly.models
   :members:
   :undoc-members:
   :show-inheritance:
   :exclude-members: utc_now, underscore, metadata, registry, type_annotation_map, CASCADE_ALL_ASYNC, CASCADE_ALL_DELETE_ORPHAN_ASYNC, TableNameMixin

.. seealso::

   :doc:`/getting_started` begins with a standard SQLAlchemy
   ``DeclarativeBase``; :doc:`/tutorial_overview` demonstrates the convenience
   bases.
