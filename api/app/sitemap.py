# Sitemap reader
# Lists a site's page URLs: safe XML parsing, same host only, one level of sitemap index, MAX_PAGES cap.

from urllib.parse import urlparse

import httpx
from defusedxml import DefusedXmlException
from defusedxml.ElementTree import ParseError, fromstring

from app.fetching import MAX_BYTES, FetchError, fetch_limited

MAX_SITEMAP_BYTES = MAX_BYTES
MAX_CHILD_SITEMAPS = 20


class SitemapError(Exception):
    pass


async def download(
    client: httpx.AsyncClient, url: str, max_bytes: int = MAX_SITEMAP_BYTES
) -> bytes:
    try:
        result = await fetch_limited(client, url, max_bytes)
    except FetchError as error:
        raise SitemapError(str(error)) from error

    if result.status_code >= 400:
        raise SitemapError(f"{url} answered with HTTP {result.status_code}")
    return result.body


def parse(xml: bytes) -> tuple[str, list[str]]:
    try:
        root = fromstring(xml)
    except (ParseError, DefusedXmlException) as error:
        raise SitemapError(f"Not valid sitemap XML: {error}") from error

    kind = root.tag.rsplit("}", 1)[-1]
    if kind not in {"urlset", "sitemapindex"}:
        raise SitemapError(f"Expected <urlset> or <sitemapindex>, found <{kind}>")

    locs = [
        element.text.strip()
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] == "loc" and element.text and element.text.strip()
    ]
    return kind, locs


def is_same_site(url: str, host: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and parsed.netloc == host


async def read_sitemap(
    client: httpx.AsyncClient,
    sitemap_url: str,
    max_pages: int,
    max_bytes: int = MAX_SITEMAP_BYTES,
) -> list[str]:
    host = urlparse(sitemap_url).netloc
    kind, locs = parse(await download(client, sitemap_url, max_bytes))

    if kind == "sitemapindex":
        page_urls: list[str] = []
        children = [loc for loc in locs if is_same_site(loc, host)][:MAX_CHILD_SITEMAPS]
        for child_url in children:
            child_kind, child_locs = parse(await download(client, child_url, max_bytes))
            if child_kind == "urlset":
                page_urls.extend(child_locs)
            if len(page_urls) >= max_pages:
                break
    else:
        page_urls = locs

    unique: list[str] = []
    for url in page_urls:
        if is_same_site(url, host) and url not in unique:
            unique.append(url)
        if len(unique) >= max_pages:
            break
    return unique
