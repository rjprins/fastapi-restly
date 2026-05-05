"""Read-only Country lookup view.

Demonstrates the "read-only resource" use-case from the matrix: the
generated create / update / delete routes are excluded so the API only
serves GETs. Reads still go through the framework's filtering / paging /
ordering pipeline.
"""

import fastapi_restly as fr

from ..models import Country
from ..schemas.lookup import CountrySchema


class CountryView(fr.AsyncRestView):
    """Read-only ISO country list — seed-only, no write routes.

    Note this view does *not* inherit ``TenantBase`` — countries are
    global lookup data and don't get scoped by tenant.
    """

    prefix = "/countries"
    model = Country
    schema = CountrySchema
    exclude_routes = [fr.ViewRoute.CREATE, fr.ViewRoute.UPDATE, fr.ViewRoute.DELETE]
