# Tests: HTTP client
# Private and internal addresses are refused, redirects included, and a checked public host is connected to by its IP.

import contextlib

import httpcore
import httpx
import pytest

from app import http_client
from app.config import Settings
from app.http_client import PublicOnlyTransport, get_http_client, is_public

pytestmark = pytest.mark.anyio

PUBLIC_IP = "93.184.215.14"

FAKE_DNS = {"shop.example": [PUBLIC_IP], "intranet.example": ["10.0.0.7"]}


class RecordingBackend(httpcore.AsyncMockBackend):
    def __init__(self, buffer: list[bytes]) -> None:
        super().__init__(buffer)
        self.hosts: list[str] = []

    async def connect_tcp(
        self,
        host,
        port,
        timeout=None,  # noqa: ASYNC109
        local_address=None,
        socket_options=None,
    ):
        self.hosts.append(host)
        return await super().connect_tcp(host, port, timeout, local_address, socket_options)


@pytest.fixture
def fake_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    async def resolve(host: str, _port: int) -> list[str]:
        return FAKE_DNS[host]

    monkeypatch.setattr(http_client, "resolve", resolve)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.7",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",  # noqa: S104
        "224.0.0.1",
        "::1",
        "fc00::1",
        "fe80::1",
        "::ffff:127.0.0.1",
        "64:ff9b::a00:7",
        "64:ff9b::7f00:1",
        "64:ff9b:1::a00:7",
        "2002:a00:7::1",
        "2002:7f00:1::1",
    ],
)
def test_private_and_internal_addresses_are_not_public(address: str) -> None:
    assert not is_public(address)


@pytest.mark.parametrize(
    "address", ["8.8.8.8", PUBLIC_IP, "2606:4700:4700::1111", "64:ff9b::808:808", "2002:808:808::1"]
)
def test_internet_addresses_are_public(address: str) -> None:
    assert is_public(address)


@pytest.mark.parametrize("url", ["http://127.0.0.1:9/", "http://localhost:9/", "http://[::1]:9/"])
async def test_local_addresses_are_refused_before_connecting(url: str) -> None:
    async with httpx.AsyncClient(transport=PublicOnlyTransport()) as client:
        with pytest.raises(httpx.ConnectError, match="private or internal address"):
            await client.get(url)


@pytest.mark.usefixtures("fake_dns")
async def test_a_public_host_is_connected_to_by_its_checked_ip() -> None:
    backend = RecordingBackend([b"HTTP/1.1 200 OK\r\n", b"Content-Length: 2\r\n", b"\r\n", b"ok"])

    async with httpx.AsyncClient(transport=PublicOnlyTransport(backend)) as client:
        response = await client.get("http://shop.example/")

    assert response.text == "ok"
    assert backend.hosts == [PUBLIC_IP]


@pytest.mark.usefixtures("fake_dns")
async def test_a_redirect_to_a_private_address_is_refused() -> None:
    backend = RecordingBackend(
        [
            b"HTTP/1.1 302 Found\r\n",
            b"Location: http://intranet.example/admin\r\n",
            b"Content-Length: 0\r\n",
            b"\r\n",
        ]
    )

    async with httpx.AsyncClient(
        transport=PublicOnlyTransport(backend), follow_redirects=True
    ) as client:
        with pytest.raises(httpx.ConnectError, match=r"intranet\.example points to .*10\.0\.0\.7"):
            await client.get("http://shop.example/")

    assert backend.hosts == [PUBLIC_IP]


async def test_the_app_client_is_public_only_unless_private_addresses_are_allowed() -> None:
    guarded = contextlib.asynccontextmanager(get_http_client)(Settings(_env_file=None))
    local = contextlib.asynccontextmanager(get_http_client)(
        Settings(_env_file=None, allow_private_addresses=True)
    )

    async with guarded as guarded_client, local as local_client:
        assert isinstance(guarded_client._transport, PublicOnlyTransport)
        assert not isinstance(local_client._transport, PublicOnlyTransport)
