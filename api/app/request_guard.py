# Request guard
# Refuses requests from other websites and other host names, so a page open in the browser can't make the local API crawl or spend the Claude budget.

from collections.abc import Collection, Mapping
from urllib.parse import urlsplit

ALLOWED_FETCH_SITES = frozenset({"same-origin", "same-site", "none"})

OTHER_WEBSITE = "The API doesn't answer requests from other websites."


def hostname_of(url: str) -> str | None:
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def refusal_reason(headers: Mapping[str, str], allowed_hosts: Collection[str]) -> str | None:
    allowed = {host.lower() for host in allowed_hosts}

    if hostname_of(f"//{headers.get('host', '')}") not in allowed:
        return f"The API only answers requests addressed to {', '.join(sorted(allowed))}."

    origin = headers.get("origin")
    if origin is not None and hostname_of(origin) not in allowed:
        return OTHER_WEBSITE

    if headers.get("sec-fetch-site", "none") not in ALLOWED_FETCH_SITES:
        return OTHER_WEBSITE

    return None
