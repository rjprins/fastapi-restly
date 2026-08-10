from fastapi import FastAPI
from httpx import Response

from fastapi_restly.testing import AsyncRestlyTestClient, RestlyTestClient

app = FastAPI()
sync_client = RestlyTestClient(app)
sync_response: Response = sync_client.get("/", assert_status_code=404)


async def use_async_client() -> None:
    async with AsyncRestlyTestClient(app) as client:
        response: Response = await client.get("/", assert_status_code=404)
        client.assert_status(response, 404)
