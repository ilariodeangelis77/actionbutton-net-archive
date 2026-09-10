#!/usr/bin/env python3
"""Verify a built static mirror before it is published.

The check verifies that local HTML/CSS/JavaScript URL references resolve inside
the build, that project-page URLs carry the expected base path, that archived
images decode successfully, and that no Wayback replay URL remains in rendered
text files.
"""

from __future__ import annotations

import argparse
import html
from html.parser import HTMLParser
import posixpath
import re
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from PIL import Image


TEXT_SUFFIXES = {".css", ".htm", ".html", ".js", ".xml"}
IMAGE_SUFFIXES = {".bmp", ".gif", ".ico", ".jpeg", ".jpg", ".png", ".svg", ".tif", ".tiff", ".webp"}
URL_ATTRIBUTES = {"action", "data-src", "href", "poster", "src"}
CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE)
JS_ROOT_STRING_RE = re.compile(r"(['\"])(/(?!/)[^'\"\r\n]*)\1")
WAYBACK_REPLAY_RE = re.compile(r"https?://web\.archive\.org/web/", re.IGNORECASE)


@dataclass(frozen=True)
class Reference:
    origin: Path
    value: str
    context: str


class ReferenceParser(HTMLParser):
    def __init__(self, origin: Path) -> None:
        super().__init__(convert_charrefs=True)
        self.origin = origin
        self.references: list[Reference] = []
        self.script_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "script":
            self.script_depth += 1
        self._collect_attributes(attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._collect_attributes(attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self.script_depth:
            self.script_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.script_depth:
            return
        for match in JS_ROOT_STRING_RE.finditer(data):
            self.references.append(Reference(self.origin, match.group(2), "inline script"))

    def _collect_attributes(self, attrs: list[tuple[str, str | None]]) -> None:
        for raw_name, value in attrs:
            if value is None:
                continue
            name = raw_name.lower()
            if name in URL_ATTRIBUTES:
                self.references.append(Reference(self.origin, value, name))
            elif name == "srcset":
                for candidate in value.split(","):
                    item = candidate.strip().split()
                    if item:
                        self.references.append(Reference(self.origin, item[0], "srcset"))
            elif name == "style":
                for match in CSS_URL_RE.finditer(value):
                    self.references.append(Reference(self.origin, match.group(2), "style"))


def normalize_base_path(value: str) -> str:
    value = value.strip()
    if not value or value == "/":
        return ""
    return "/" + value.strip("/")


def collect_references(root: Path) -> tuple[list[Reference], list[str]]:
    references: list[Reference] = []
    replay_hits: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        relative = path.relative_to(root)
        if WAYBACK_REPLAY_RE.search(text):
            replay_hits.append(relative.as_posix())
        if path.suffix.lower() in {".htm", ".html", ".xml"}:
            parser = ReferenceParser(relative)
            parser.feed(text)
            parser.close()
            references.extend(parser.references)
        if path.suffix.lower() in {".css", ".htm", ".html", ".xml"}:
            for match in CSS_URL_RE.finditer(text):
                references.append(Reference(relative, match.group(2), "CSS url()"))
    return references, replay_hits


def local_reference_path(reference: Reference, base_path: str) -> tuple[PurePosixPath | None, str | None]:
    value = html.unescape(reference.value).strip()
    if not value or value.startswith(("#", "data:", "javascript:", "mailto:", "tel:", "blob:")):
        return None, None

    parts = urlsplit(value)
    if parts.scheme or parts.netloc:
        return None, None
    url_path = unquote(parts.path)
    if not url_path:
        return PurePosixPath(reference.origin.as_posix()), None

    if url_path.startswith("/"):
        if base_path:
            if url_path == base_path:
                url_path = "/"
            elif url_path.startswith(base_path + "/"):
                url_path = url_path[len(base_path) :]
            else:
                return None, f"root URL lacks base path {base_path}: {reference.value}"
        normalized = posixpath.normpath(url_path).lstrip("/")
    else:
        parent = PurePosixPath(reference.origin.as_posix()).parent.as_posix()
        normalized = posixpath.normpath(posixpath.join(parent, url_path))

    if normalized == ".":
        normalized = ""
    if normalized == ".." or normalized.startswith("../"):
        return None, f"URL escapes build root: {reference.value}"
    return PurePosixPath(normalized), None


def reference_exists(root: Path, relative: PurePosixPath) -> bool:
    target = root.joinpath(*relative.parts)
    if target.is_file():
        return True
    if target.is_dir() and (target / "index.html").is_file():
        return True
    if not target.suffix and (target / "index.html").is_file():
        return True
    return False


def image_error(path: Path) -> str | None:
    if not path.stat().st_size:
        return "empty file"
    if path.suffix.lower() == ".svg":
        start = path.read_bytes()[:4096].decode("utf-8", errors="ignore").lower()
        if "<svg" not in start:
            return "invalid SVG"
        return None
    try:
        with Image.open(path) as image:
            image.verify()
        return None
    except Exception as error:
        return f"image decoder rejected payload: {error}"


def verify(root: Path, base_path: str) -> list[str]:
    errors: list[str] = []
    references, replay_hits = collect_references(root)
    for path in replay_hits:
        errors.append(f"{path}: contains a Wayback replay URL")

    seen: set[tuple[str, str, str]] = set()
    for reference in references:
        relative, error = local_reference_path(reference, base_path)
        if error:
            key = (reference.origin.as_posix(), reference.value, error)
            if key not in seen:
                seen.add(key)
                errors.append(f"{key[0]}: {error} ({reference.context})")
        elif relative is not None and not reference_exists(root, relative):
            detail = f"missing local target: {reference.value} ({reference.context})"
            key = (reference.origin.as_posix(), reference.value, detail)
            if key not in seen:
                seen.add(key)
                errors.append(f"{key[0]}: {detail}")

    image_count = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        image_count += 1
        error = image_error(path)
        if error:
            errors.append(f"{path.relative_to(root).as_posix()}: {error}")

    print(
        f"Integrity audit: files={sum(1 for path in root.rglob('*') if path.is_file())} "
        f"references={len(references)} images={image_count} errors={len(errors)}"
    )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("_site"))
    parser.add_argument("--base-path", default="")
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_dir():
        parser.error(f"build directory does not exist: {root}")

    errors = verify(root, normalize_base_path(args.base_path))
    if errors:
        print("Integrity audit failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Integrity audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
