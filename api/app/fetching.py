# Size-limited downloads
# Shared by the sitemap reader and the page fetcher, so no response can grow past its size cap.

from dataclasses import dataclass

import httpx

MAX_BYTES = 5_000_000


class FetchError(Exception):
    pass


@dataclass
class FetchResult:
    url: str

    status_code: int

    content_type: str

    body: bytes


async def fetch_limited(
    client: httpx.AsyncClient, url: str, max_bytes: int = MAX_BYTES
) -> FetchResult:
    try:
        async with client.stream("GET", url) as response:
            result = FetchResult(
                url=str(response.url),
                status_code=response.status_code,
                content_type=response.headers.get("content-type", ""),
                body=b"",
            )
            if response.status_code >= 400:
                return result
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise FetchError(f"{url} is larger than {max_bytes:,} bytes")
            result.body = bytes(body)
            return result
    except httpx.HTTPError as error:
        raise FetchError(f"Could not download {url}: {error}") from error
