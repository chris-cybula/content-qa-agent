# Meta description check
# Present, and 50 to 160 characters long.

from app.checks.finding import FindingData, shorten
from app.pages import PageData

MIN_LENGTH = 50

MAX_LENGTH = 160

ELEMENT = "meta description"


def check_meta_description(page: PageData) -> list[FindingData]:
    description = page.meta_description

    if not description:
        return [
            FindingData(
                page_url=page.url,
                rule="missing-meta-description",
                severity="medium",
                element=ELEMENT,
                evidence="No meta description"
                if description is None
                else "Meta description is empty",
                suggested_fix=f"Add a meta description of {MIN_LENGTH} to {MAX_LENGTH} characters that sums up the page.",
            )
        ]

    if len(description) < MIN_LENGTH:
        return [
            FindingData(
                page_url=page.url,
                rule="meta-too-short",
                severity="low",
                element=ELEMENT,
                evidence=f'"{description}" is {len(description)} characters (minimum {MIN_LENGTH})',
                suggested_fix=f"Expand it to {MIN_LENGTH} to {MAX_LENGTH} characters: say what the page offers.",
            )
        ]

    if len(description) > MAX_LENGTH:
        return [
            FindingData(
                page_url=page.url,
                rule="meta-too-long",
                severity="low",
                element=ELEMENT,
                evidence=f'{len(description)} characters (maximum {MAX_LENGTH}): "{shorten(description)}"',
                suggested_fix=f"Shorten it to {MAX_LENGTH} characters or fewer so search results show all of it.",
            )
        ]

    return []
