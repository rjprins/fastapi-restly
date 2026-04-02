"""Label and TaskLabel views."""

import sqlalchemy as sa

import fastapi_restly as fr

from ..models import Label, TaskLabel
from ..schemas import LabelSchema, TaskLabelSchema
from ._base import TenantBase


class LabelView(TenantBase):
    """CRUD for labels (organization-scoped).

    Demonstrates:
    - V2 query modifier style (``name=urgent`` instead of ``filter[name]=urgent``)
    - ``make_new_object`` to stamp ``organization_id`` from the auth context
      rather than trusting the client to supply the correct tenant
    - ``delete_object`` to clean up task-label associations before removal
    """

    prefix = "/labels"
    model = Label
    schema = LabelSchema
    query_modifier_version = fr.QueryModifierVersion.V2

    async def on_list(self, query_params, query=None):
        """Scope labels to the current organization."""
        org_id = self._current_org_id()
        if org_id is not None:
            query = sa.select(Label).where(Label.organization_id == org_id)
        return await super().on_list(query_params, query)

    async def make_new_object(self, schema_obj):
        """Stamp organization_id from the auth context.

        In production, the org ID comes from the auth token — the client should
        not be able to create labels in another tenant's org. When no auth
        context is set (tests, development), the client-provided value is used.
        """
        obj = await super().make_new_object(schema_obj)
        auth_org_id = self._current_org_id()
        if auth_org_id is not None:
            obj.organization_id = auth_org_id
        return obj

    async def delete_object(self, obj):
        """Remove all task-label associations before deleting the label.

        Overriding ``delete_object`` is the right place for cascading cleanup
        that should happen on every delete — here we detach any tasks that
        reference this label before removing the label row itself.
        """
        await self.session.execute(
            sa.delete(TaskLabel).where(TaskLabel.label_id == obj.id)
        )
        await super().delete_object(obj)


class TaskLabelView(TenantBase):
    """CRUD for task-label associations.

    Demonstrates ``make_new_object`` to stamp the ``added_by_id`` field from
    the request context rather than requiring the client to provide it.
    """

    prefix = "/task-labels"
    model = TaskLabel
    schema = TaskLabelSchema

    async def make_new_object(self, schema_obj):
        """Stamp added_by_id from auth context if the client did not provide it."""
        obj = await super().make_new_object(schema_obj)
        if obj.added_by_id is None:
            # In production: obj.added_by_id = self.request.state.user_id
            obj.added_by_id = getattr(self.request.state, "user_id", None)
        return obj
