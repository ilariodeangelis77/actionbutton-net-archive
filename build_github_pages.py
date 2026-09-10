#!/usr/bin/env python3
"""Build the static mirror for a GitHub Pages project URL."""

from __future__ import annotations

import argparse
import html
import re
import shutil
from pathlib import Path


TEXT_SUFFIXES = {".css", ".htm", ".html", ".xml"}
HTML_ROOT_ATTRIBUTE = re.compile(
    r"(?P<prefix>\b(?:href|src|action|poster|data-src)\s*=\s*)"
    r"(?P<quote>['\"])(?P<url>/(?!/)[^'\"\r\n]*)(?P=quote)",
    re.I,
)
SRCSET_ATTRIBUTE = re.compile(
    r"(?P<prefix>\bsrcset\s*=\s*)(?P<quote>['\"])(?P<value>[^'\"\r\n]*)(?P=quote)",
    re.I,
)
CSS_ROOT_URL = re.compile(
    r"(?P<prefix>url\(\s*)(?P<quote>['\"]?)(?P<url>/(?!/)[^)'\"\s]+)"
    r"(?P=quote)(?P<suffix>\s*\))",
    re.I,
)
SCRIPT_BLOCK = re.compile(
    r"(?P<open><script\b[^>]*>)(?P<body>.*?)(?P<close></script\s*>)",
    re.I | re.S,
)
JS_ROOT_STRING = re.compile(
    r"(?P<quote>['\"])(?P<url>/(?!/)[^'\"\r\n]*)(?P=quote)",
)
BODY_OPEN = re.compile(r"(<body\b[^>]*>)", re.I)


def normalize_base_path(value: str) -> str:
    value = value.strip()
    if not value or value == "/":
        return ""
    return "/" + value.strip("/")


def prefix_srcset(value: str, base_path: str) -> str:
    candidates = []
    for candidate in value.split(","):
        leading = candidate[: len(candidate) - len(candidate.lstrip())]
        content = candidate.lstrip()
        already_prefixed = content == base_path or content.startswith(base_path + "/")
        if content.startswith("/") and not content.startswith("//") and not already_prefixed:
            content = base_path + content
        candidates.append(leading + content)
    return ",".join(candidates)


def prefix_root_urls(text: str, base_path: str, suffix: str) -> str:
    if not base_path:
        return text

    if suffix in {".htm", ".html", ".xml"}:
        def prefix_script(match: re.Match[str]) -> str:
            body = JS_ROOT_STRING.sub(
                lambda url_match: (
                    f"{url_match.group('quote')}{base_path}"
                    f"{url_match.group('url')}{url_match.group('quote')}"
                )
                if not url_match.group("url").startswith(base_path + "/")
                else url_match.group(0),
                match.group("body"),
            )
            return f"{match.group('open')}{body}{match.group('close')}"

        text = SCRIPT_BLOCK.sub(prefix_script, text)
        text = HTML_ROOT_ATTRIBUTE.sub(
            lambda match: (
                f"{match.group('prefix')}{match.group('quote')}"
                f"{base_path}{match.group('url')}{match.group('quote')}"
            )
            if not (
                match.group("url") == base_path
                or match.group("url").startswith(base_path + "/")
            )
            else match.group(0),
            text,
        )
        text = SRCSET_ATTRIBUTE.sub(
            lambda match: (
                f"{match.group('prefix')}{match.group('quote')}"
                f"{prefix_srcset(match.group('value'), base_path)}{match.group('quote')}"
            ),
            text,
        )

    return CSS_ROOT_URL.sub(
        lambda match: (
            f"{match.group('prefix')}{match.group('quote')}{base_path}"
            f"{match.group('url')}{match.group('quote')}{match.group('suffix')}"
        )
        if not (
            match.group("url") == base_path
            or match.group("url").startswith(base_path + "/")
        )
        else match.group(0),
        text,
    )


def mirror_notice(base_path: str) -> str:
    home = html.escape(f"{base_path}/", quote=True)
    return (
        '<div id="archival-mirror-notice" role="note" '
        'style="box-sizing:border-box;width:100%;padding:8px 12px;'
        'background:#fff3cd;color:#332701;border-bottom:1px solid #d6b84c;'
        'font:13px/1.4 Arial,sans-serif;text-align:center;position:relative;z-index:2147483647">'
        f'<a href="{home}" style="color:#332701;font-weight:bold">Archival mirror</a> '
        'preserved for research and educational use. This copy is not affiliated with '
        'Action Button; the original content belongs to its respective authors.'
        '</div>'
    )


def rewrite_file(path: Path, base_path: str, add_notice: bool) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return

    text = prefix_root_urls(text, base_path, path.suffix.lower())
    if add_notice and path.suffix.lower() in {".htm", ".html"}:
        notice = mirror_notice(base_path)
        text, replacements = BODY_OPEN.subn(r"\1" + notice, text, count=1)
        if not replacements:
            text = notice + text
    path.write_text(text, encoding="utf-8", newline="")


def build(source: Path, destination: Path, base_path: str, add_notice: bool) -> None:
    source = source.resolve()
    destination = destination.resolve()
    if not source.is_dir():
        raise SystemExit(f"Source directory does not exist: {source}")
    if source == destination or source in destination.parents:
        raise SystemExit("Destination must not be the source or a child of the source")
    if destination == Path(destination.anchor):
        raise SystemExit(f"Refusing unsafe destination: {destination}")

    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)

    rewritten = 0
    for path in destination.rglob("*"):
        if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
            rewrite_file(path, base_path, add_notice)
            rewritten += 1
    (destination / ".nojekyll").touch()
    print(
        f"Built {destination} from {source}: {rewritten} text files rewritten "
        f"for base path {base_path or '/'}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("actionbutton-site"))
    parser.add_argument("--destination", type=Path, default=Path("_site"))
    parser.add_argument("--base-path", default="")
    parser.add_argument(
        "--no-notice",
        action="store_true",
        help="Do not add the archival/non-affiliation notice to rendered pages.",
    )
    args = parser.parse_args()
    build(
        args.source,
        args.destination,
        normalize_base_path(args.base_path),
        not args.no_notice,
    )


if __name__ == "__main__":
    main()
