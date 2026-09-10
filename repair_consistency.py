#!/usr/bin/env python3
"""Apply offline-consistency repairs to the completed Action Button mirror."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import html
import re
import shutil
import sqlite3
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup, Comment, XMLParsedAsHTMLWarning
import warnings


warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

MISSING_REVIEWS = {"1054", "2488", "2838", "3187", "3284"}
OBSOLETE_SCRIPT_MARKERS = (
    "s7.addthis.com/js/",
    "/wp/wp-admin/admin-ajax.php?action=addthis_",
    "/wp/wp-includes/js/wp-emoji-release",
    "apis.google.com/js/plusone",
    "platform.linkedin.com/in.js",
    "platform.twitter.com/widgets.js",
    "stumbleupon.com/hostedbadge.php",
    "connect.facebook.net/",
    "/better-wp-security/modules/free/strong-passwords/js/strong-passwords.js",
)


def unavailable_page(title: str, detail: str, back_href: str, back_label: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>body{{max-width:46rem;margin:10vh auto;padding:2rem;background:#111;color:#eee;
font:18px/1.55 Georgia,serif}}a{{color:#9ddcff}}code{{color:#ffd28a}}</style></head>
<body><h1>{html.escape(title)}</h1><p>{html.escape(detail)}</p>
<p><a href="{html.escape(back_href)}">{html.escape(back_label)}</a></p></body></html>
"""


def process_html(root: Path) -> tuple[int, int, int]:
    changed = 0
    removed_scripts = 0
    disabled_links = 0
    review_pattern = re.compile(r"^/p/(1054|2488|2838|3187|3284)/$")
    podcast_pattern = re.compile(r"^/podcasts/abnet\d{2}\.mp3$")
    attachment_target = "/query/__q_attachment_id-498_7874b31b2d/"

    for path in root.rglob("*.html"):
        source = path.read_text(encoding="utf-8", errors="replace")
        soup = BeautifulSoup(source, "html.parser")
        touched = False

        for script in list(soup.find_all("script", src=True)):
            src = str(script.get("src", "")).strip()
            if src == "#" or any(marker in src for marker in OBSOLETE_SCRIPT_MARKERS):
                script.decompose()
                removed_scripts += 1
                touched = True

        for script in list(soup.find_all("script", src=False)):
            body = script.get_text("", strip=False)
            if "_wpemojiSettings" in body or "wpEmojiSettingsSupports" in body:
                script.decompose()
                removed_scripts += 1
                touched = True
            elif 'myimages[' in body and '="sb/' in body:
                revised = re.sub(r'(["\'])sb/(\d{2}\.gif)\1', r'\1/sb/\2\1', body)
                if revised != body:
                    script.string = revised
                    touched = True

        for iframe in list(soup.find_all("iframe", src=True)):
            if "facebook.com/plugins/like.php" in str(iframe.get("src", "")):
                iframe.decompose()
                touched = True

        for comment in list(soup.find_all(string=lambda value: isinstance(value, Comment))):
            if "Typography.com Gotham" in str(comment):
                comment.extract()
                touched = True

        for link in list(soup.find_all("link", href=True)):
            href = str(link.get("href", ""))
            rel = {str(item).lower() for item in (link.get("rel") or [])}
            if "dns-prefetch" in rel and any(host in href for host in ("s.w.org", "s7.addthis.com")):
                link.decompose()
                touched = True
            elif "stylesheet" in rel and (
                "fonts.googleapis.com" in href
                or "cloud.typography.com/7754072/7118552/css/fonts.css" in href
                or "www.actionbutton.net/wp/wp-admin/" in href
            ):
                # Login/admin captures are non-functional static artifacts; do
                # not leave them dependent on remote admin or unavailable font
                # services. The open local substitute is loaded by the theme.
                link.decompose()
                touched = True

        for anchor in soup.find_all("a", href=True):
            href = str(anchor.get("href", ""))
            review = review_pattern.match(href)
            if review:
                anchor["href"] = f"/_unavailable/review-{review.group(1)}.html"
                anchor["title"] = "The archived review capture is unavailable"
                touched = True
                disabled_links += 1
            elif href == attachment_target:
                anchor["href"] = "/_unavailable/attachment-498.html"
                anchor["title"] = "The archived attachment capture is unavailable"
                touched = True
                disabled_links += 1
            elif podcast_pattern.match(href):
                del anchor["href"]
                anchor["class"] = list(anchor.get("class") or []) + ["archive-unavailable"]
                anchor["title"] = "Audio payload unavailable in the Wayback Machine"
                touched = True
                disabled_links += 1
            elif href == "/wp/__q_paged-2_b6d924c735/":
                anchor["href"] = "/query/__q_paged-2_b6d924c735/"
                anchor["title"] = "Older posts in the recovered main archive"
                touched = True
                disabled_links += 1

        for form in soup.find_all("form"):
            action = str(form.get("action", ""))
            if action in {"wp-login.php", "http://www.actionbutton.net/wp/wp-login.php"}:
                form["action"] = "#"
                form["onsubmit"] = "return false"
                touched = True

        if touched:
            path.write_text(soup.decode(formatter="html"), encoding="utf-8", newline="")
            changed += 1
    return changed, removed_scripts, disabled_links


def recover_sidebar_images(root: Path) -> tuple[int, list[str]]:
    target_dir = root / "sb"
    target_dir.mkdir(parents=True, exist_ok=True)

    def recover(number: int) -> tuple[str, bool]:
        name = f"{number:02}.gif"
        target = target_dir / name
        if target.is_file() and target.read_bytes().startswith((b"GIF87a", b"GIF89a")):
            return name, True
        url = (
            "https://web.archive.org/web/20101206080045id_/"
            f"http://actionbutton.net/sb/{name}"
        )
        for attempt in range(4):
            try:
                response = requests.get(
                    url,
                    headers={"User-Agent": "ActionButtonLocalMirror/1.2"},
                    timeout=30,
                )
                if response.status_code == 200 and response.content.startswith((b"GIF87a", b"GIF89a")):
                    target.write_bytes(response.content)
                    return name, True
                if response.status_code not in {429, 500, 502, 503, 504}:
                    break
            except requests.RequestException:
                pass
            time.sleep(0.5 * (2 ** attempt))
        return name, False

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(recover, range(1, 87)))
    return sum(ok for _name, ok in results), [name for name, ok in results if not ok]


def limit_sidebar_slideshow(root: Path) -> int:
    """Ensure the random slideshow selects only GIFs present on disk."""
    available = sorted((root / "sb").glob("[0-9][0-9].gif"))
    if not available:
        return 0
    replacement = "\n".join(
        f'  myimages[{index}]="/sb/{path.name}"'
        for index, path in enumerate(available, start=1)
    ) + "\n"
    changed = 0
    pattern = re.compile(
        r'(?:[ \t]*myimages\[\d+\]=["\']/sb/\d{2}\.gif["\'][ \t]*\r?\n?)+'
    )
    for path in root.rglob("*.html"):
        source = path.read_text(encoding="utf-8", errors="replace")
        if "var myimages=new Array()" not in source:
            continue
        revised, count = pattern.subn(replacement, source, count=1)
        if count and revised != source:
            path.write_text(revised, encoding="utf-8", newline="")
            changed += 1
    return changed


def write_fallbacks(root: Path) -> None:
    target = root / "_unavailable"
    target.mkdir(parents=True, exist_ok=True)
    for review_id in sorted(MISSING_REVIEWS, key=int):
        (target / f"review-{review_id}.html").write_text(
            unavailable_page(
                f"Review {review_id} is unavailable",
                "The link existed in Action Button's review index, but the Wayback Machine has no retrievable page payload for it.",
                "/page/175/",
                "Return to the review archive",
            ), encoding="utf-8", newline="",
        )
    (target / "attachment-498.html").write_text(
        unavailable_page(
            "Attachment 498 is unavailable",
            "The original attachment target has no retrievable page payload in the Wayback Machine.",
            "/p/497/",
            "Return to the related page",
        ), encoding="utf-8", newline="",
    )
    registration = root / "wp" / "wp-login" / "__q_action-register_7ab6a613d6" / "index.html"
    if registration.is_file() and "Fatal error" in registration.read_text(encoding="utf-8", errors="replace"):
        registration.write_text(
            unavailable_page(
                "Registration is unavailable",
                "This archived WordPress registration endpoint contained only a server-side fatal error and cannot function in a static mirror.",
                "/",
                "Return to Action Button",
            ), encoding="utf-8", newline="",
        )


def quarantine_orphan(root: Path) -> bool:
    source = root / "wp" / "wp-content" / "uploads" / "2008" / "11" / "[gametitle].GIF"
    if not source.is_file():
        return False
    target = root / "_unavailable" / "source-artifacts" / "gametitle-placeholder.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    shutil.move(str(source), str(target))
    return True


def sync_database(root: Path) -> None:
    database = sqlite3.connect(root / ".mirror-state.sqlite3")
    repaired = {
        "http://www.actionbutton.net/images/simsoc/thereforeheckthisgame.png": "images\\simsoc\\thereforeheckthisgame.png",
        "http://www.actionbutton.net/podcasts/itunes.gif": "podcasts\\itunes.gif",
        "http://www.actionbutton.net/podcasts/xml.gif": "podcasts\\xml.gif",
        "http://www.actionbutton.net/wp/rocket.gif": "wp\\rocket.gif",
        "http://www.actionbutton.net/wp/star.gif": "wp\\star.gif",
    }
    for url, local_path in repaired.items():
        path = root / local_path
        if path.is_file():
            database.execute(
                """UPDATE items SET state='done', kind='asset', local_path=?, http_status=200,
                   content_type='image/gif', error=NULL, updated_at=CURRENT_TIMESTAMP WHERE url=?""",
                (local_path, url),
            )
    database.execute(
        """UPDATE items SET state='missing', local_path=NULL,
           error='Original page contains an unrecoverable placeholder image URL',
           updated_at=CURRENT_TIMESTAMP
           WHERE url LIKE '%/wp-content/uploads/2008/11/[gametitle].GIF%' ESCAPE '\\'"""
    )
    database.commit()
    database.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("actionbutton-site"))
    parser.add_argument(
        "--recover-sidebar",
        action="store_true",
        help="Resume downloading optional old-theme sidebar GIF variants from Wayback",
    )
    args = parser.parse_args()
    root = args.output.resolve()
    changed, removed, disabled = process_html(root)
    limit_sidebar_slideshow(root)
    if args.recover_sidebar:
        recovered_sidebar, missing_sidebar = recover_sidebar_images(root)
    else:
        present = {path.name for path in (root / "sb").glob("[0-9][0-9].gif")}
        recovered_sidebar = len(present)
        missing_sidebar = [f"{number:02}.gif" for number in range(1, 87) if f"{number:02}.gif" not in present]
    limit_sidebar_slideshow(root)
    write_fallbacks(root)
    quarantined = quarantine_orphan(root)
    sync_database(root)
    print(f"changed_html={changed}")
    print(f"removed_obsolete_scripts={removed}")
    print(f"rewritten_or_disabled_dead_links={disabled}")
    print(f"quarantined_orphan={quarantined}")
    print(f"sidebar_images_available={recovered_sidebar}/86")
    print(f"sidebar_images_missing={','.join(missing_sidebar)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
