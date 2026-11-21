# Finding
# The finding every check returns, plus helpers that describe page elements the same way everywhere.

from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel

from app.pages import Image, Link

Severity = Literal["high", "medium", "low"]


class FindingData(BaseModel):
    page_url: str

    rule: str

    severity: Severity

    element: str

    evidence: str

    suggested_fix: str


def shorten(text: str, limit: int = 60) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def file_name(url: str) -> str:
    name = urlparse(url).path.rsplit("/", 1)[-1]
    return name or url


def describe_image(image: Image) -> str:
    return f"img {file_name(image.src)}"


def describe_link(link: Link) -> str:
    if link.kind == "internal":
        parsed = urlparse(link.href)
        target = parsed.path or "/"
        if parsed.query:
            target += f"?{parsed.query}"
        return f"a to {target}"
    return f"a to {link.href}"
