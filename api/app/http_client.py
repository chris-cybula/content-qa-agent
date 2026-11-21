# HTTP client
# Fetches sitemaps, pages, links and images with a generic User-Agent and a timeout. Unless ALLOW_PRIVATE_ADDRESSES
# is on, it reaches public IPs only: each host, redirects included, is resolved, checked and connected to by that IP.

import contextlib
import ipaddress
import socket
import ssl
from collections.abc import AsyncIterator, Iterable
from functools import lru_cache
from typing import Annotated

import anyio
import httpcore
import httpx
from fastapi import Depends

from app.config import Settings, get_settings

USER_AGENT = "ContentQAAgent/0.1"

SocketOption = (
    tuple[int, int, int] | tuple[int, int, bytes | bytearray] | tuple[int, int, None, int]
)


NAT64 = ipaddress.IPv6Network("64:ff9b::/96")

LOCAL_NAT64 = ipaddress.IPv6Network("64:ff9b:1::/48")


def wrapped_ipv4(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip.sixtofour is not None:
        return ip.sixtofour
    if ip in NAT64:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return None


def is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address):
        if ip in LOCAL_NAT64:
            return False
        wrapped = wrapped_ipv4(ip)
        if wrapped is not None:
            ip = wrapped
    return ip.is_global and not ip.is_multicast


async def resolve(host: str, port: int) -> list[str]:
    infos = await anyio.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


async def public_addresses(host: str, port: int, lookup_seconds: float | None) -> list[str]:
    try:
        with anyio.fail_after(lookup_seconds):
            addresses = await resolve(host, port)
    except TimeoutError as error:
        raise httpcore.ConnectTimeout(f"Looking up {host} took too long") from error
    except OSError as error:
        raise httpcore.ConnectError(f"Can't look up {host}: {error}") from error

    private = [address for address in addresses if not is_public(address)]
    if private or not addresses:
        raise httpcore.ConnectError(
            f"{host} points to a private or internal address ({', '.join(private) or 'none'}), "
            "so it isn't fetched. Set ALLOW_PRIVATE_ADDRESSES=true to crawl a local site."
        )
    return addresses


class PublicOnlyBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, backend: httpcore.AsyncNetworkBackend | None = None) -> None:
        self._backend = backend or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109
        local_address: str | None = None,
        socket_options: Iterable[SocketOption] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        *others, last = await public_addresses(host, port, timeout)
        for address in others:
            with contextlib.suppress(httpcore.ConnectError):
                return await self._backend.connect_tcp(
                    address, port, timeout, local_address, socket_options
                )
        return await self._backend.connect_tcp(last, port, timeout, local_address, socket_options)

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


@lru_cache
def tls_context() -> ssl.SSLContext:
    return httpx.create_ssl_context()


class PublicOnlyTransport(httpx.AsyncHTTPTransport):
    def __init__(self, backend: httpcore.AsyncNetworkBackend | None = None) -> None:
        context = tls_context()
        super().__init__(verify=context)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=context,
            network_backend=PublicOnlyBackend(backend),
        )


async def get_http_client(
    settings: Annotated[Settings, Depends(get_settings)],
) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
        timeout=settings.http_timeout_seconds,
        transport=None if settings.allow_private_addresses else PublicOnlyTransport(),
    ) as client:
        yield client
