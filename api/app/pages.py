# Page fetcher and parser
# Fetches pages a few at a time and pulls out title, meta, headings, links, images and visible text.

import asyncio
import re
from typing import Literal
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel, Field

from app.fetching import FetchError, fetch_limited

HIDDEN_TAGS = ["script", "style", "noscript", "template"]
HEADING_TAG = re.compile(r"^h[1-6]$")


class Heading(BaseModel):
    level: int

    text: str


class Link(BaseModel):
    href: str

    text: str

    kind: Literal["internal", "external", "other"]


class Image(BaseModel):
    src: str

    alt: str | None


class PageData(BaseModel):
    url: str

    final_url: str | None = None

    status_code: int | None = None

    error: str | None = None

    title: str | None = None

    meta_description: str | None = None

    lang: str | None = None

    headings: list[Heading] = Field(default_factory=list)

    links: list[Link] = Field(default_factory=list)

    images: list[Image] = Field(default_factory=list)

    text: str = ""

    word_count: int = 0


def tidy(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def attribute_text(tag: Tag, name: str) -> str | None:
    value = tag.get(name)
    if value is None:
        return None
    return value if isinstance(value, str) else " ".join(value)


def link_kind(href: str, host: str) -> Literal["internal", "external", "other"]:
    parsed = urlparse(href)
    if parsed.scheme not in {"http", "https"}:
        return "other"
    return "internal" if parsed.netloc == host else "external"


def accessible_name(anchor: Tag) -> str:
    text = tidy(anchor.get_text(" "))
    if text:
        return text
    label = attribute_text(anchor, "aria-label")
    if label and tidy(label):
        return tidy(label)
    image = anchor.find("img", alt=True)
    return tidy(attribute_text(image, "alt") or "") if image else ""


def parse_html(
    html: bytes | str, url: str, final_url: str | None = None, status_code: int | None = 200
) -> PageData:
    soup = BeautifulSoup(html, "html.parser")

    base = final_url or url
    base_tag = soup.find("base", href=True)
    if base_tag:
        base = urljoin(base, attribute_text(base_tag, "href") or "")
    host = urlparse(base).netloc

    title = tidy(soup.title.get_text()) if soup.title else None
    meta = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    meta_description = tidy(attribute_text(meta, "content") or "") if meta else None
    lang = attribute_text(soup.html, "lang") if soup.html else None

    headings = [
        Heading(level=int(tag.name[1]), text=tidy(tag.get_text(" ")))
        for tag in soup.find_all(HEADING_TAG)
    ]

    links = []
    for anchor in soup.find_all("a", href=True):
        href = (attribute_text(anchor, "href") or "").strip()
        if not href or href.startswith("#"):
            continue
        resolved = urljoin(base, href)
        links.append(
            Link(href=resolved, text=accessible_name(anchor), kind=link_kind(resolved, host))
        )

    images = []
    for img in soup.find_all("img", src=True):
        src = (attribute_text(img, "src") or "").strip()
        alt = attribute_text(img, "alt")
        images.append(Image(src=urljoin(base, src), alt=tidy(alt) if alt is not None else None))

    for tag in soup(HIDDEN_TAGS):
        tag.decompose()
    text = tidy((soup.body or soup).get_text(" "))

    return PageData(
        url=url,
        final_url=final_url or url,
        status_code=status_code,
        title=title,
        meta_description=meta_description,
        lang=lang,
        headings=headings,
        links=links,
        images=images,
        text=text,
        word_count=len(text.split()),
    )


async def fetch_page(client: httpx.AsyncClient, url: str) -> PageData:
    try:
        result = await fetch_limited(client, url)
    except FetchError as error:
        return PageData(url=url, error=str(error))

    if result.status_code >= 400:
        return PageData(
            url=url,
            final_url=result.url,
            status_code=result.status_code,
            error=f"HTTP {result.status_code}",
        )

    if "html" not in result.content_type.lower():
        reason = result.content_type or "no content type"
        return PageData(
            url=url,
            final_url=result.url,
            status_code=result.status_code,
            error=f"Not an HTML page ({reason})",
        )

    return parse_html(result.body, url=url, final_url=result.url, status_code=result.status_code)


async def fetch_pages(
    client: httpx.AsyncClient, urls: list[str], concurrency: int
) -> list[PageData]:
    semaphore = asyncio.Semaphore(concurrency)

    async def fetch_one(url: str) -> PageData:
        async with semaphore:
            return await fetch_page(client, url)

    return list(await asyncio.gather(*(fetch_one(url) for url in urls)))
