#!/usr/bin/env python3
"""Repair image files and external image references in an Action Button mirror.

The archived site contains a handful of URLs whose path text was sanitized
after the original images had been captured.  It also embeds images hosted on
other domains.  This script restores the former from their original captures,
downloads the latter into ``_external/``, rewrites the HTML/CSS references, and
produces a repeatable audit report.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import mimetypes
import re
import shutil
import time
import warnings
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlsplit

import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from PIL import Image


TARGET_TIMESTAMP = "20230206182305"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico", ".tif", ".tiff"}
CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE)
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# local URL -> (capture timestamp, unsanitized original URL)
ARCHIVED_REPAIRS = {
    "/podcasts/itunes.gif": (
        "20071003045016",
        "http://www.actionbutton.net/podcasts/itunes.gif",
    ),
    "/podcasts/xml.gif": (
        "20071003045004",
        "http://www.actionbutton.net/podcasts/xml.gif",
    ),
    "/wp/wp-content/uploads/2007/04/finalfantasystuffstuffstuff1.png": (
        "20120823173440",
        "http://www.actionbutton.net/wp/wp-content/uploads/2007/04/finalfantasyshitshitshit1.png",
    ),
    "/wp/wp-content/uploads/2007/04/finalfantasystuffstuffstuff2.PNG": (
        "20110622045949",
        "http://www.actionbutton.net/wp/wp-content/uploads/2007/04/finalfantasyshitshitshit2.PNG",
    ),
    "/wp/wp-content/uploads/2009/06/whattheheck.bmp": (
        "20120622065946",
        "http://www.actionbutton.net/wp/wp-content/uploads/2009/06/whatthefuck.bmp",
    ),
    "/wp/wp-content/uploads/2011/02/massiveheck.gif": (
        "20120212013402",
        "http://www.actionbutton.net/wp/wp-content/uploads/2011/02/massivefuck.gif",
    ),
    "/wp/wp-content/uploads/2011/04/holyheck.gif": (
        "20111004220654",
        "http://www.actionbutton.net/wp/wp-content/uploads/2011/04/holyfuck.gif",
    ),
    "/images/simsoc/thereforeheckthisgame.png": (
        "20111013082635",
        "http://www.actionbutton.net/images/simsoc/thereforefuckthisgame.png",
    ),
    "/wp/wp-content/themes/abdn55555/images/actionbutton800.png": (
        "20120530065329",
        "http://www.actionbutton.net/wp/wp-content/themes/abdn55555/images/actionbutton800.png",
    ),
}

# These hosts' nearest 2023 replay is an error or an HTML landing page, while
# an older capture still contains the actual image payload.
EXTERNAL_CAPTURE_REPAIRS = {
    "http://odeo.com/img/badge-channel-black.gif": "20100601170815",
    "http://www.podfeed.net/images/add_podcast.gif": "20071007090647",
    "http://img142.imageshack.us/img142/8229/supremecommander36028mv8.gif": "20130115103325",
    "http://www.largeprimenumbers.com/files/heavenlysword.GIF": "20120527044615",
    "http://www.insertcredit.com/archives/pacmanworld3.jpg": "20090416225332",
    "http://www.insertcredit.com/archives/levelup.jpg": "20090416225329",
    "http://www.insertcredit.com/archives/porislbox.jpg": "20090416225331",
}

# This domain appears only inside a 2024/2025 third-party redirect body that
# was accidentally stored for a 2010 archive URL. It is not Action Button
# content and must not be pulled into the preservation copy.
IGNORED_EXTERNAL_HOSTS = {"cinderellaaffair.org"}


def valid_image(data: bytes) -> bool:
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        return True
    except Exception:
        return False


def local_target(root: Path, public_url: str) -> Path:
    return root / Path(*PurePosixPath(unquote(urlsplit(public_url).path).lstrip("/")).parts)


def external_target(root: Path, url: str, content_type: str = "") -> tuple[Path, str]:
    parts = urlsplit(url)
    path = unquote(parts.path).strip("/") or "image"
    path = "/".join(re.sub(r'[<>:"|?*\\]', "_", item) for item in path.split("/"))
    suffix = PurePosixPath(path).suffix.lower()
    if suffix not in IMAGE_EXTENSIONS:
        guessed = mimetypes.guess_extension(content_type.split(";", 1)[0].strip()) or ".img"
        path += guessed
    if parts.query:
        digest = hashlib.sha1(parts.query.encode()).hexdigest()[:10]
        item = PurePosixPath(path)
        path = str(item.with_name(f"{item.stem}__q_{digest}{item.suffix}"))
    rel = PurePosixPath("_external") / parts.netloc.lower() / PurePosixPath(path)
    return root / Path(*rel.parts), "/" + quote(rel.as_posix(), safe="/;,@-._~!$&'()*+=")


def request_image(session: requests.Session, url: str, timeout: float) -> tuple[bytes, str, str] | None:
    candidates = []
    if url in EXTERNAL_CAPTURE_REPAIRS:
        candidates.append(
            f"https://web.archive.org/web/{EXTERNAL_CAPTURE_REPAIRS[url]}id_/{url}"
        )
    candidates.append(f"https://web.archive.org/web/{TARGET_TIMESTAMP}id_/{url}")
    live = url
    if live.startswith("http://"):
        live = "https://" + live[7:]
    candidates.append(live)
    for candidate in candidates:
        try:
            response = session.get(candidate, timeout=timeout, allow_redirects=True)
            if response.status_code == 200 and valid_image(response.content):
                return response.content, response.headers.get("Content-Type", ""), response.url
        except requests.RequestException:
            pass
    return None


def collect_external_refs(root: Path) -> set[str]:
    urls: set[str] = set()
    for path in root.rglob("*.html"):
        soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="replace"), "html.parser")
        for tag in soup.find_all(["img", "source", "video", "input"]):
            for attr in ("src", "poster"):
                value = tag.get(attr)
                if value and urlsplit(value).scheme in {"http", "https"}:
                    urls.add(value)
            for item in str(tag.get("srcset", "")).split(","):
                value = item.strip().split()[0] if item.strip() else ""
                if urlsplit(value).scheme in {"http", "https"}:
                    urls.add(value)
    for path in root.rglob("*.css"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in CSS_URL_RE.finditer(text):
            value = match.group(2).strip()
            if urlsplit(value).scheme in {"http", "https"}:
                urls.add(value)
    return urls


def rewrite_urls(root: Path, replacements: dict[str, str]) -> int:
    changed = 0
    for pattern in ("*.html", "*.css"):
        for path in root.rglob(pattern):
            text = path.read_text(encoding="utf-8", errors="replace")
            revised = text
            for old, new in replacements.items():
                revised = revised.replace(old, new)
                revised = revised.replace(old.replace("&", "&amp;"), new)
            if revised != text:
                path.write_text(revised, encoding="utf-8", newline="")
                changed += 1
    return changed


def repair_unavailable_markup(root: Path) -> list[str]:
    """Use recovered badges when present, otherwise keep honest text fallbacks."""
    changes: list[str] = []
    podcast_path = root / "podcasts" / "index.html"
    if podcast_path.is_file():
        soup = BeautifulSoup(podcast_path.read_text(encoding="utf-8", errors="replace"), "html.parser")
        for filename, label in (("itunes.gif", "iTunes"), ("xml.gif", "XML feed")):
            image = soup.find("img", src=f"/podcasts/{filename}")
            badge = soup.find(
                "span",
                class_="archive-text-badge",
                string=lambda value: bool(value and value.strip() == label),
            )
            target = root / "podcasts" / filename
            recovered = target.is_file() and valid_image(target.read_bytes())
            if recovered and badge:
                image = soup.new_tag("img")
                image["src"] = f"/podcasts/{filename}"
                image["alt"] = label
                image["border"] = "0"
                badge.replace_with(image)
                changes.append(f"/podcasts/{filename} (restored archived image)")
            elif not recovered and image:
                badge = soup.new_tag("span")
                badge["class"] = "archive-text-badge"
                badge["style"] = (
                    "display:inline-block;padding:1px 5px;border:1px solid #888;"
                    "background:#eee;color:#222;font:11px Arial,sans-serif;line-height:15px"
                )
                badge.string = label
                image.replace_with(badge)
                changes.append(f"/podcasts/{filename} (text fallback)")
        podcast_path.write_text(str(soup), encoding="utf-8", newline="")

    placeholder_path = root / "p" / "321" / "index.html"
    if placeholder_path.is_file():
        soup = BeautifulSoup(placeholder_path.read_text(encoding="utf-8", errors="replace"), "html.parser")
        image = soup.find("img", src="/wp/wp-content/uploads/2008/11/[gametitle].GIF")
        if image:
            image.decompose()
            placeholder_path.write_text(str(soup), encoding="utf-8", newline="")
            changes.append("/wp/wp-content/uploads/2008/11/[gametitle].GIF (removed source placeholder)")
    return changes


def audit(root: Path) -> dict[str, object]:
    missing: dict[str, list[str]] = {}
    corrupt: dict[str, list[str]] = {}
    external: dict[str, list[str]] = {}
    ignored_external: dict[str, list[str]] = {}
    occurrences = 0

    def check(origin: Path, value: str) -> None:
        nonlocal occurrences
        if not value or value.startswith(("data:", "#", "javascript:")):
            return
        occurrences += 1
        parts = urlsplit(value)
        rel_origin = origin.relative_to(root).as_posix()
        if parts.scheme in {"http", "https"} or parts.netloc:
            bucket = ignored_external if parts.hostname in IGNORED_EXTERNAL_HOSTS else external
            bucket.setdefault(value, []).append(rel_origin)
            return
        target = local_target(root, value) if parts.path.startswith("/") else (origin.parent / unquote(parts.path))
        if not target.is_file():
            missing.setdefault(value, []).append(rel_origin)
            return
        if target.suffix.lower() in IMAGE_EXTENSIONS:
            try:
                if not valid_image(target.read_bytes()):
                    corrupt.setdefault(value, []).append(rel_origin)
            except OSError:
                corrupt.setdefault(value, []).append(rel_origin)

    for path in root.rglob("*.html"):
        soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="replace"), "html.parser")
        for tag in soup.find_all(["img", "source", "video", "input"]):
            for attr in ("src", "poster"):
                if tag.get(attr):
                    check(path, str(tag[attr]))
            for item in str(tag.get("srcset", "")).split(","):
                if item.strip():
                    check(path, item.strip().split()[0])
    for path in root.rglob("*.css"):
        for match in CSS_URL_RE.finditer(path.read_text(encoding="utf-8", errors="replace")):
            check(path, match.group(2).strip())
    return {
        "image_reference_occurrences": occurrences,
        "missing": missing,
        "corrupt": corrupt,
        "external": external,
        "ignored_external": ignored_external,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("actionbutton-site"))
    parser.add_argument("--delay", type=float, default=0.1)
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--skip-external", action="store_true")
    args = parser.parse_args()
    root = args.output.resolve()
    session = requests.Session()
    session.headers["User-Agent"] = "ActionButtonLocalMirror/1.1 (personal archival copy)"

    repaired: list[str] = []
    unavailable: list[str] = []
    for public_url, (timestamp, original) in ARCHIVED_REPAIRS.items():
        replay = f"https://web.archive.org/web/{timestamp}id_/{original}"
        try:
            response = session.get(replay, timeout=args.timeout, allow_redirects=True)
            if response.status_code == 200 and valid_image(response.content):
                target = local_target(root, public_url)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(response.content)
                repaired.append(public_url)
            else:
                unavailable.append(public_url)
        except requests.RequestException:
            unavailable.append(public_url)
        time.sleep(args.delay)

    for name in ("rocket.gif", "star.gif"):
        source = root / name
        target = root / "wp" / name
        if source.is_file() and valid_image(source.read_bytes()):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            repaired.append("/wp/" + name)

    replacements: dict[str, str] = {}
    if not args.skip_external:
        for url in sorted(collect_external_refs(root)):
            if urlsplit(url).hostname in IGNORED_EXTERNAL_HOSTS:
                continue
            result = request_image(session, url, args.timeout)
            if result is None:
                unavailable.append(url)
                continue
            data, content_type, _source = result
            target, public = external_target(root, url, content_type)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            replacements[url] = public
            repaired.append(url)
            time.sleep(args.delay)
        rewrite_urls(root, replacements)

    markup_fallbacks = repair_unavailable_markup(root)

    report = audit(root)
    report.update({
        "repaired_count": len(repaired),
        "localized_external_count": len(replacements),
        "unavailable_during_repair": unavailable,
        "markup_fallbacks": markup_fallbacks,
    })
    report_path = root / "image-audit.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "repaired": len(repaired),
        "localized_external": len(replacements),
        "missing": len(report["missing"]),
        "corrupt": len(report["corrupt"]),
        "external": len(report["external"]),
        "report": str(report_path),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
