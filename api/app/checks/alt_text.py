# Alt text check
# Images with no alt attribute, and alt text that is just a file name.

import re
from pathlib import PurePosixPath

from app.checks.finding import FindingData, describe_image, file_name
from app.pages import Image, PageData

IMAGE_FILE = re.compile(r"^\S+\.(png|jpe?g|gif|webp|svg|avif|bmp|tiff?|heic)$", re.IGNORECASE)

CAMERA_NAME = re.compile(r"^(img|dsc|dscn|dscf|pxl|dcim)[_-]?\d+([_-]\d+)*$", re.IGNORECASE)


def looks_like_file_name(alt: str, src: str) -> bool:
    if IMAGE_FILE.match(alt) or CAMERA_NAME.match(alt):
        return True

    stem = PurePosixPath(file_name(src)).stem
    return alt.lower() == stem.lower() and any(separator in alt for separator in "-_")


def check_image(page_url: str, image: Image) -> FindingData | None:
    if image.alt is None:
        return FindingData(
            page_url=page_url,
            rule="missing-alt",
            severity="high",
            element=describe_image(image),
            evidence="No alt attribute",
            suggested_fix='Add alt text that describes the image, or alt="" if it is only decoration.',
        )

    if looks_like_file_name(image.alt, image.src):
        return FindingData(
            page_url=page_url,
            rule="alt-is-filename",
            severity="medium",
            element=describe_image(image),
            evidence=f'Alt is "{image.alt}"',
            suggested_fix="Replace the file name with a short description of what the image shows.",
        )

    return None


def check_alt_text(page: PageData) -> list[FindingData]:
    findings = (check_image(page.url, image) for image in page.images)
    return [finding for finding in findings if finding is not None]
