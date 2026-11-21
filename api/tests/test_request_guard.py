# Tests: request guard
# Other websites, other host names and cross-site browser requests get 403; local callers and the API's own docs page get through.

import httpx
import pytest

from app.request_guard import refusal_reason

pytestmark = pytest.mark.anyio

LOCAL = ("localhost", "127.0.0.1", "::1")


@pytest.mark.parametrize(
    "headers",
    [
        {"host": "localhost:8000"},
        {"host": "127.0.0.1:8000"},
        {"host": "[::1]:8000"},
        {"host": "LOCALHOST:8000"},
        {"host": "127.0.0.1:8000", "origin": "http://127.0.0.1:8000"},
        {"host": "localhost:8000", "origin": "http://localhost:3000"},
        {"host": "127.0.0.1:8000", "sec-fetch-site": "same-origin"},
        {"host": "127.0.0.1:8000", "sec-fetch-site": "none"},
    ],
)
def test_local_requests_get_through(headers: dict[str, str]) -> None:
    assert refusal_reason(headers, LOCAL) is None


@pytest.mark.parametrize(
    "headers",
    [
        {"host": "evil.example:8000"},
        {},
        {"host": "127.0.0.1:8000", "origin": "https://evil.example"},
        {"host": "127.0.0.1:8000", "origin": "null"},
        {"host": "127.0.0.1:8000", "origin": "http://[::1"},
        {"host": "127.0.0.1:8000", "sec-fetch-site": "cross-site"},
    ],
)
def test_other_websites_and_hosts_are_refused(headers: dict[str, str]) -> None:
    assert refusal_reason(headers, LOCAL) is not None


async def test_a_form_post_from_another_website_gets_403(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/runs/1/ai-checks",
        headers={"origin": "https://evil.example", "sec-fetch-site": "cross-site"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "The API doesn't answer requests from other websites."}


async def test_a_rebound_host_name_gets_403(client: httpx.AsyncClient) -> None:
    response = await client.get("/runs", headers={"host": "evil.example:8000"})

    assert response.status_code == 403


async def test_the_web_app_and_the_docs_page_get_through(client: httpx.AsyncClient) -> None:
    server_call = await client.get("/health")
    docs_page_call = await client.get(
        "/health", headers={"origin": "http://localhost:8000", "sec-fetch-site": "same-origin"}
    )

    assert server_call.status_code == 200
    assert docs_page_call.status_code == 200
