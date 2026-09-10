#!/usr/bin/env python3
"""Create a resumable static mirror of a site from the Wayback Machine.

The crawler is intentionally conservative: requests are sequential and delayed,
the queue/state live in SQLite, and completed downloads are never fetched again.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import mimetypes
import re
import sqlite3
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Optional
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup


WAYBACK_RE = re.compile(
    r"^https?://web\.archive\.org/web/\d+(?:[a-z]{2}_)?/(https?://.*)$",
    re.IGNORECASE,
)
CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE)
CSS_IMPORT_RE = re.compile(
    r"(@import\s+)(?!url\()(['\"])(.*?)\2", re.IGNORECASE
)
HTML_EXTENSIONS = {"", ".html", ".htm", ".php", ".asp", ".aspx"}
ASSET_EXTENSIONS = {
    ".css", ".js", ".mjs", ".json", ".xml", ".txt", ".pdf", ".zip",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp",
    ".avif", ".woff", ".woff2", ".ttf", ".otf", ".eot", ".mp3", ".ogg",
    ".wav", ".mp4", ".webm", ".mov", ".avi", ".swf",
}
SKIP_PATH_PREFIXES = (
    "/wp-admin", "/wp-login", "/xmlrpc.php", "/wp-json", "/wp-cron.php",
)
SKIP_PATH_SUFFIXES = ("/feed", "/trackback", "/comments/feed")
SKIP_QUERY_KEYS = {
    "rest_route", "replytocom", "doing_wp_cron", "preview", "customize_changeset_uuid",
    "feed",
}
DROP_QUERY_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid",
}
HTML_ATTRS = ("href", "src", "poster", "data-src", "data-lazy-src")


@dataclass(frozen=True)
class LocalizedURL:
    original: str
    fragment: str
    path: Path
    public_path: str
    kind: str


class MirrorCrawler:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.output = args.output.resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.db_path = self.output / ".mirror-state.sqlite3"
        self.db = sqlite3.connect(self.db_path)
        self.db.row_factory = sqlite3.Row
        self._init_db()
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "ActionButtonLocalMirror/1.0 (personal archival copy)",
                "Accept-Encoding": "gzip, deflate",
            }
        )
        self.last_request_at = 0.0
        self.target_hosts = {h.lower() for h in args.host}
        self.canonical_host = args.host[0].lower()
        self.stats = {"done": 0, "missing": 0, "failed": 0, "skipped": 0}

    def _init_db(self) -> None:
        self.db.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS items (
                url TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 10,
                state TEXT NOT NULL DEFAULT 'pending',
                local_path TEXT,
                discovered_from TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                http_status INTEGER,
                content_type TEXT,
                capture_url TEXT,
                error TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_items_queue
                ON items(state, priority);
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS capture_overrides (
                url TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                original TEXT NOT NULL
            );
            """
        )
        self.db.commit()

    def close(self) -> None:
        self.db.close()
        self.session.close()

    def unwrap_wayback(self, value: str) -> str:
        match = WAYBACK_RE.match(value)
        return match.group(1) if match else value

    def normalize(self, value: str, base: Optional[str] = None) -> tuple[Optional[str], str]:
        value = html.unescape(value.strip())
        if not value or value.startswith(("#", "mailto:", "tel:", "javascript:", "data:", "blob:")):
            return None, ""
        if any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value):
            return None, ""
        value = self.unwrap_wayback(value)
        if value.startswith("//"):
            value = "http:" + value
        if base:
            value = urljoin(base, value)
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        if host not in self.target_hosts:
            return None, parts.fragment
        path = re.sub(r"/{2,}", "/", parts.path or "/")
        query_items = []
        for key, val in parse_qsl(parts.query, keep_blank_values=True):
            if key.lower() in DROP_QUERY_KEYS:
                continue
            query_items.append((key, val))
        query_items.sort()
        query = urlencode(query_items, doseq=True)
        normalized = urlunsplit(("http", self.canonical_host, path, query, ""))
        return normalized, parts.fragment

    def should_skip(self, url: str) -> bool:
        if any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in url):
            return True
        parts = urlsplit(url)
        lower_path = parts.path.lower().rstrip("/") or "/"
        if any(lower_path.startswith(prefix) for prefix in SKIP_PATH_PREFIXES):
            return True
        if ("/wp-admin/" in lower_path or "/wp-json/" in lower_path
                or lower_path.endswith("/wp-login.php")
                or lower_path.endswith("/wp-trackback.php")):
            return True
        if any(marker in lower_path for marker in ("<a", "&acirc;", "”http:", "@actionbutton.net")):
            return True
        if lower_path.endswith("/xmlrpc.php"):
            return True
        if any(lower_path.endswith(suffix) for suffix in SKIP_PATH_SUFFIXES):
            return True
        if lower_path.endswith(("/feed/atom", "/feed/rss", "/feed/rss2")):
            return True
        if lower_path.endswith("wp-comments-post.php"):
            return True
        query_keys = {key.lower() for key, _ in parse_qsl(parts.query, keep_blank_values=True)}
        stripped_suffix = PurePosixPath(parts.path.rstrip("/")).suffix.lower()
        if "paged" in query_keys and stripped_suffix in ASSET_EXTENSIONS:
            return True
        if ".css/" in lower_path:
            return True
        return bool(query_keys & SKIP_QUERY_KEYS)

    def guess_kind(self, url: str, hint: Optional[str] = None) -> str:
        suffix = PurePosixPath(urlsplit(url).path).suffix.lower()
        if suffix == ".css":
            return "css"
        if suffix in ASSET_EXTENSIONS:
            return "asset"
        if hint in {"page", "asset", "css"}:
            return hint
        return "page"

    def localize(self, url: str, fragment: str = "", hint: Optional[str] = None) -> LocalizedURL:
        parts = urlsplit(url)
        kind = self.guess_kind(url, hint)
        path = parts.path or "/"
        suffix = PurePosixPath(path).suffix.lower()

        if kind in {"asset", "css"}:
            local = "/".join(
                unquote(part).replace("/", "_").replace("\\", "_")
                for part in path.lstrip("/").split("/")
            ) or "asset"
            if local.endswith("/"):
                local += "index"
            if not suffix:
                guessed = mimetypes.guess_extension(mimetypes.guess_type(path)[0] or "")
                if guessed:
                    local += guessed
            # Cache-busting ?ver= values do not create distinct local assets.
            significant = [(k, v) for k, v in parse_qsl(parts.query) if k.lower() != "ver"]
            if significant:
                digest = hashlib.sha1(parts.query.encode()).hexdigest()[:10]
                stem, ext = str(PurePosixPath(local).with_suffix("")), PurePosixPath(local).suffix
                local = f"{stem}__q_{digest}{ext}"
        else:
            query = dict(parse_qsl(parts.query, keep_blank_values=True))
            if path in {"", "/", "/index.php"} and "p" in query and query["p"].isdigit():
                local = f"p/{query['p']}/index.html"
            elif path in {"", "/", "/index.php"} and "page_id" in query and query["page_id"].isdigit():
                local = f"page/{query['page_id']}/index.html"
            elif path in {"", "/", "/index.php"} and "m" in query and query["m"].isdigit():
                val = query["m"]
                archive_root = f"archive/{val[:4]}/{val[4:6] or 'all'}"
                page = query.get("paged", "")
                if page.isdigit() and int(page) > 1:
                    local = f"{archive_root}/page/{page}/index.html"
                else:
                    local = f"{archive_root}/index.html"
            elif not parts.query:
                clean = path.lstrip("/")
                if not clean:
                    local = "index.html"
                elif suffix in {".html", ".htm"}:
                    local = clean
                elif suffix in {".php", ".asp", ".aspx"}:
                    local = clean.rsplit(".", 1)[0] + "/index.html"
                else:
                    local = clean.rstrip("/") + "/index.html"
            else:
                readable = "-".join(
                    f"{re.sub(r'[^A-Za-z0-9_-]+', '-', k).strip('-')}-{re.sub(r'[^A-Za-z0-9_-]+', '-', v).strip('-')}"
                    for k, v in parse_qsl(parts.query, keep_blank_values=True)
                ).strip("-")[:90]
                digest = hashlib.sha1(parts.query.encode()).hexdigest()[:10]
                base_path = path.lstrip("/").rstrip("/")
                if PurePosixPath(base_path).suffix:
                    base_path = str(PurePosixPath(base_path).with_suffix(""))
                base_path = base_path or "query"
                local = f"{base_path}/__q_{readable or 'query'}_{digest}/index.html"

        local = re.sub(r"[<>:\"|?*]", "_", local)
        local_path = self.output / Path(*PurePosixPath(local).parts)
        public = "/" + quote(
            PurePosixPath(local).as_posix(), safe="/;,@-._~!$&'()*+="
        )
        if public == "/index.html":
            public = "/"
        elif public.endswith("/index.html"):
            public = public[: -len("index.html")]
        if fragment:
            public += "#" + quote(fragment, safe="-._~!$&'()*+,;=:@/?")
        return LocalizedURL(url, fragment, local_path, public, kind)

    def enqueue(self, value: str, base: Optional[str] = None, hint: Optional[str] = None,
                discovered_from: Optional[str] = None) -> Optional[str]:
        normalized, _ = self.normalize(value, base)
        if not normalized:
            return None
        if self.should_skip(normalized):
            self.stats["skipped"] += 1
            return None
        kind = self.guess_kind(normalized, hint)
        priority = {"page": 0, "css": 1, "asset": 2}[kind]
        local = self.localize(normalized, hint=kind)
        self.db.execute(
            """
            INSERT INTO items(url, kind, priority, local_path, discovered_from)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                priority = MIN(priority, excluded.priority),
                kind = CASE WHEN items.kind = 'asset' AND excluded.kind IN ('page','css')
                            THEN excluded.kind ELSE items.kind END
            """,
            (normalized, kind, priority, str(local.path.relative_to(self.output)), discovered_from),
        )
        self.db.commit()
        return normalized

    def next_item(self) -> Optional[sqlite3.Row]:
        return self.db.execute(
            """
            SELECT * FROM items
            WHERE state = 'pending'
            ORDER BY priority ASC, rowid ASC
            LIMIT 1
            """
        ).fetchone()

    def fetch(self, original: str) -> requests.Response:
        override = self.db.execute(
            "SELECT timestamp, original FROM capture_overrides WHERE url=?", (original,)
        ).fetchone()
        if not override:
            override = self.db.execute(
                "SELECT timestamp, original FROM capture_overrides WHERE url=?",
                (self.capture_key(original),),
            ).fetchone()
        if override:
            replay = f"https://web.archive.org/web/{override['timestamp']}id_/{override['original']}"
        else:
            replay = f"https://web.archive.org/web/{self.args.timestamp}id_/{original}"
        error: Optional[Exception] = None
        for attempt in range(1, self.args.retries + 1):
            since = time.monotonic() - self.last_request_at
            if since < self.args.delay:
                time.sleep(self.args.delay - since)
            try:
                self.last_request_at = time.monotonic()
                response = self.session.get(
                    replay,
                    timeout=(self.args.connect_timeout, self.args.read_timeout),
                    allow_redirects=True,
                )
                if response.status_code in {429, 500, 502, 503, 504}:
                    raise requests.HTTPError(f"temporary HTTP {response.status_code}", response=response)
                return response
            except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as exc:
                error = exc
                if attempt < self.args.retries:
                    time.sleep(min(self.args.backoff * (2 ** (attempt - 1)), 30.0))
        assert error is not None
        raise error

    def rewrite_reference(self, value: str, base: str, hint: Optional[str],
                          discovered_from: str) -> str:
        normalized, fragment = self.normalize(value, base)
        if not normalized or self.should_skip(normalized):
            return value
        self.enqueue(normalized, hint=hint, discovered_from=discovered_from)
        return self.localize(normalized, fragment, hint).public_path

    def rewrite_srcset(self, value: str, base: str, discovered_from: str) -> str:
        entries = []
        for item in value.split(","):
            item = item.strip()
            if not item:
                continue
            bits = item.split()
            bits[0] = self.rewrite_reference(bits[0], base, "asset", discovered_from)
            entries.append(" ".join(bits))
        return ", ".join(entries)

    def rewrite_css_text(self, text: str, base: str, discovered_from: str) -> str:
        def replace_url(match: re.Match[str]) -> str:
            raw = match.group(2).strip()
            rewritten = self.rewrite_reference(raw, base, "asset", discovered_from)
            quote_char = match.group(1) or ""
            return f"url({quote_char}{rewritten}{quote_char})"

        def replace_import(match: re.Match[str]) -> str:
            rewritten = self.rewrite_reference(match.group(3), base, "css", discovered_from)
            return f"{match.group(1)}{match.group(2)}{rewritten}{match.group(2)}"

        text = CSS_URL_RE.sub(replace_url, text)
        return CSS_IMPORT_RE.sub(replace_import, text)

    def rewrite_html(self, body: bytes, base: str) -> bytes:
        soup = BeautifulSoup(body, "html.parser")

        # Raw (id_) replay normally has no Wayback chrome. Remove its known
        # instrumentation if a particular capture contains it anyway.
        for node in list(soup.select("script[src], link[href]")):
            ref = node.get("src") or node.get("href") or ""
            if "web.archive.org" in ref and not WAYBACK_RE.match(ref):
                node.decompose()
        for node in list(soup.find_all("script")):
            if "__wm" in (node.string or "") or "archive_analytics" in (node.string or ""):
                node.decompose()

        for tag in soup.find_all(True):
            for attr in HTML_ATTRS:
                if not tag.has_attr(attr):
                    continue
                hint: Optional[str]
                if attr == "href" and tag.name == "a":
                    hint = "page"
                elif attr == "href" and tag.name == "link" and "stylesheet" in (tag.get("rel") or []):
                    hint = "css"
                elif attr in {"src", "poster", "data-src", "data-lazy-src"}:
                    hint = "asset"
                else:
                    hint = None
                tag[attr] = self.rewrite_reference(str(tag[attr]), base, hint, base)
            if tag.has_attr("srcset"):
                tag["srcset"] = self.rewrite_srcset(str(tag["srcset"]), base, base)
            if tag.has_attr("style"):
                tag["style"] = self.rewrite_css_text(str(tag["style"]), base, base)

        for style in soup.find_all("style"):
            if style.string:
                style.string.replace_with(self.rewrite_css_text(style.string, base, base))

        for meta in soup.select("meta[http-equiv]"):
            if str(meta.get("http-equiv", "")).lower() == "refresh" and meta.get("content"):
                content = str(meta["content"])
                match = re.search(r"(?i)(url\s*=\s*)(.+)$", content)
                if match:
                    new_url = self.rewrite_reference(match.group(2).strip(" '\""), base, "page", base)
                    meta["content"] = content[: match.start(2)] + new_url

        return soup.encode("utf-8", formatter="html")

    @staticmethod
    def response_kind(response: requests.Response, fallback: str) -> str:
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
        if content_type in {"text/html", "application/xhtml+xml"}:
            return "page"
        if content_type == "text/css":
            return "css"
        return fallback

    def process(self, row: sqlite3.Row) -> None:
        url = row["url"]
        self.db.execute(
            "UPDATE items SET state='downloading', attempts=attempts+1, updated_at=CURRENT_TIMESTAMP WHERE url=?",
            (url,),
        )
        self.db.commit()
        try:
            response = self.fetch(url)
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            replay_target = self.unwrap_wayback(response.url)
            replay_host = (urlsplit(replay_target).hostname or "").lower()
            if response.status_code == 200 and replay_host and replay_host not in self.target_hosts:
                self.db.execute(
                    """UPDATE items SET state='missing', http_status=?, content_type=?, capture_url=?,
                       error=?, updated_at=CURRENT_TIMESTAMP WHERE url=?""",
                    (response.status_code, content_type, response.url,
                     f"Archived capture redirects outside target hosts: {replay_host}", url),
                )
                self.db.commit()
                self.stats["missing"] += 1
                return
            if response.status_code != 200:
                state = "missing" if response.status_code in {404, 410, 451} else "failed"
                self.db.execute(
                    """UPDATE items SET state=?, http_status=?, content_type=?, capture_url=?,
                       error=?, updated_at=CURRENT_TIMESTAMP WHERE url=?""",
                    (state, response.status_code, content_type, response.url,
                     f"HTTP {response.status_code}", url),
                )
                self.db.commit()
                self.stats[state] += 1
                return

            kind = self.response_kind(response, row["kind"])
            localized = self.localize(url, hint=kind)
            body = response.content
            if kind == "page":
                body = self.rewrite_html(body, url)
            elif kind == "css":
                encoding = response.encoding or "utf-8"
                text = body.decode(encoding, errors="replace")
                body = self.rewrite_css_text(text, url, url).encode("utf-8")

            localized.path.parent.mkdir(parents=True, exist_ok=True)
            if localized.path.is_dir():
                # An earlier interrupted classification may have created
                # <asset>/index.html. Remove only that known generated shape.
                children = list(localized.path.iterdir())
                if len(children) == 1 and children[0].name == "index.html":
                    children[0].unlink()
                    localized.path.rmdir()
                else:
                    raise RuntimeError(f"refusing to replace non-empty directory: {localized.path}")
            localized.path.write_bytes(body)
            self.db.execute(
                """UPDATE items SET state='done', kind=?, local_path=?, http_status=?,
                   content_type=?, capture_url=?, error=NULL, updated_at=CURRENT_TIMESTAMP WHERE url=?""",
                (kind, str(localized.path.relative_to(self.output)), response.status_code,
                 content_type, response.url, url),
            )
            self.db.commit()
            self.stats["done"] += 1
        except Exception as exc:  # Preserve progress and continue the rest of the site.
            self.db.execute(
                "UPDATE items SET state='failed', error=?, updated_at=CURRENT_TIMESTAMP WHERE url=?",
                (f"{type(exc).__name__}: {exc}"[:2000], url),
            )
            self.db.commit()
            self.stats["failed"] += 1

    def reset_retryable(self) -> None:
        self.db.execute("UPDATE items SET state='pending' WHERE state IN ('downloading','failed')")
        self.db.commit()

    def prune_skipped_pending(self) -> None:
        rows = self.db.execute("SELECT url FROM items").fetchall()
        skipped = [(row["url"],) for row in rows if self.should_skip(row["url"])]
        if skipped:
            self.db.executemany("DELETE FROM items WHERE url=?", skipped)
            self.db.commit()

    def repair_misclassified_downloads(self) -> None:
        """Correct file links that appeared in anchor tags and looked like pages."""
        rows = self.db.execute("SELECT url, kind, state, local_path FROM items").fetchall()
        for row in rows:
            inferred = self.guess_kind(row["url"])
            old_path = self.output / row["local_path"] if row["local_path"] else None
            was_directory_mapping = bool(
                row["local_path"] and str(row["local_path"]).lower().endswith("index.html")
            )
            path_conflict = bool(old_path and old_path.is_dir())
            if (row["kind"] == "page" and inferred in {"asset", "css"}
                    and (was_directory_mapping or path_conflict)):
                localized = self.localize(row["url"], hint=inferred)
                self.db.execute(
                    """UPDATE items SET kind=?, priority=?, state='pending', local_path=?,
                       error=NULL, updated_at=CURRENT_TIMESTAMP WHERE url=?""",
                    (inferred, 1 if inferred == "css" else 2,
                     str(localized.path.relative_to(self.output)), row["url"]),
                )
        self.db.commit()

    def repair_local_asset_links(self) -> None:
        extensions = "|".join(re.escape(ext.lstrip(".")) for ext in sorted(ASSET_EXTENSIONS))
        pattern = re.compile(
            rf"(?P<quote>['\"])(?P<url>/[^'\"]+\.(?:{extensions}))/(?P<tail>#[^'\"]*)?(?P=quote)",
            re.IGNORECASE,
        )
        for path in self.output.rglob("*.html"):
            text = path.read_text(encoding="utf-8", errors="replace")
            repaired = pattern.sub(
                lambda match: f"{match.group('quote')}{match.group('url')}{match.group('tail') or ''}{match.group('quote')}",
                text,
            )
            if repaired != text:
                path.write_text(repaired, encoding="utf-8")

        dynamic_local = re.compile(
            r"(?P<quote>['\"])/(?:wp/(?:wp-login|wp-trackback|wp-admin)/|"
            r"&acirc;|&rdquo;|_a(?:%20| )href=)[^'\"]*(?P=quote)",
            re.IGNORECASE,
        )
        for path in self.output.rglob("*.html"):
            text = path.read_text(encoding="utf-8", errors="replace")
            repaired = dynamic_local.sub(lambda m: f"{m.group('quote')}#{m.group('quote')}", text)
            repaired = repaired.replace('href="/ario@actionbutton.net/"', 'href="mailto:ario@actionbutton.net"')
            repaired = repaired.replace("href='/ario@actionbutton.net/'", "href='mailto:ario@actionbutton.net'")
            if repaired != text:
                path.write_text(repaired, encoding="utf-8")

        # Some legacy CSS uses empty query-string cache busters on fonts and
        # images. If an earlier run mapped one to a hashed filename but the
        # unqualified local asset exists, point at that existing file.
        hashed_asset = re.compile(
            r"(?P<base>/[^'\"()\s]+?)__q_[0-9a-f]{10}(?P<ext>\.[A-Za-z0-9]+)",
            re.IGNORECASE,
        )
        for path in list(self.output.rglob("*.html")) + list(self.output.rglob("*.css")):
            text = path.read_text(encoding="utf-8", errors="replace")

            def replace_hashed(match: re.Match[str]) -> str:
                public = match.group("base") + match.group("ext")
                candidate = self.output / unquote(public.lstrip("/"))
                return public if candidate.is_file() else match.group(0)

            repaired = hashed_asset.sub(replace_hashed, text)
            if repaired != text:
                path.write_text(repaired, encoding="utf-8")

    def prune_duplicate_local_paths(self) -> None:
        self.db.execute(
            """DELETE FROM items
               WHERE state != 'done'
                 AND EXISTS (
                     SELECT 1 FROM items AS complete
                     WHERE complete.state='done'
                       AND complete.local_path=items.local_path
                 )"""
        )
        self.db.commit()

    def repair_percent_encoded_paths(self) -> None:
        for path in sorted(self.output.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if not path.is_file() or "%" not in str(path.relative_to(self.output)):
                continue
            rel = path.relative_to(self.output)
            decoded_parts = [unquote(part).replace("/", "_").replace("\\", "_") for part in rel.parts]
            target = self.output.joinpath(*decoded_parts)
            if target == path or target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            path.replace(target)
        for row in self.db.execute("SELECT url, kind FROM items").fetchall():
            localized = self.localize(row["url"], hint=row["kind"])
            self.db.execute(
                "UPDATE items SET local_path=? WHERE url=?",
                (str(localized.path.relative_to(self.output)), row["url"]),
            )
        self.db.commit()

    def load_capture_indexes(self) -> None:
        index_paths = list(self.args.capture_index or [])
        if self.args.post_capture_index:
            index_paths.append(self.args.post_capture_index)
        if not index_paths:
            return
        captures: dict[str, tuple[str, str]] = {}
        for raw_path in index_paths:
            index_path = raw_path.resolve()
            if not index_path.exists():
                raise FileNotFoundError(f"capture index not found: {index_path}")
            rows = json.loads(index_path.read_text(encoding="utf-8"))
            if not rows or rows[0][:2] != ["timestamp", "original"]:
                raise ValueError(f"unexpected CDX capture index format: {index_path}")
            for row in rows[1:]:
                timestamp, original = row[0], row[1]
                normalized, _ = self.normalize(original)
                if not normalized:
                    continue
                key = self.capture_key(normalized)
                current = captures.get(key)
                if current is None or timestamp > current[0]:
                    captures[key] = (timestamp, original)

        self.db.executemany(
            "INSERT OR REPLACE INTO capture_overrides(url, timestamp, original) VALUES(?,?,?)",
            [(key, timestamp, original) for key, (timestamp, original) in captures.items()],
        )

        recovered = 0
        missing = self.db.execute(
            "SELECT url, state, capture_url FROM items WHERE state IN ('missing','failed')"
        ).fetchall()
        for row in missing:
            key = self.capture_key(row["url"])
            if key not in captures:
                continue
            timestamp, original = captures[key]
            if (row["state"] == "missing" and row["capture_url"]
                    and f"/web/{timestamp}" in row["capture_url"]):
                continue
            self.db.execute(
                "INSERT OR REPLACE INTO capture_overrides(url, timestamp, original) VALUES(?,?,?)",
                (row["url"], timestamp, original),
            )
            self.db.execute(
                "UPDATE items SET state='pending', error=NULL WHERE url=?", (row["url"],)
            )
            recovered += 1
        self.db.commit()
        if recovered:
            print(f"Queued {recovered} dead 2023 resources from older successful captures.", flush=True)

    @staticmethod
    def capture_key(url: str) -> str:
        """Match cache-busted URLs to the same archived file capture."""
        parts = urlsplit(url)
        path = parts.path
        if path != "/":
            path = path.rstrip("/")
        query = urlencode(
            [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() not in {"ver", "iefix", "paged"}],
            doseq=True,
        )
        return urlunsplit((parts.scheme, parts.netloc, path, query, ""))

    def run(self) -> None:
        self.repair_misclassified_downloads()
        self.prune_skipped_pending()
        self.load_capture_indexes()
        if self.args.retry_failed:
            self.reset_retryable()
            self.prune_skipped_pending()
        for seed in self.args.seed:
            self.enqueue(seed, hint="page", discovered_from="seed")

        processed = 0
        while self.args.max_items <= 0 or processed < self.args.max_items:
            item = self.next_item()
            if item is None:
                break
            processed += 1
            print(
                f"[{processed:05d}] {item['kind']:<5} {item['url']}",
                flush=True,
            )
            self.process(item)

        self.repair_percent_encoded_paths()
        self.repair_local_asset_links()
        self.prune_duplicate_local_paths()
        self.write_reports()

    def write_reports(self) -> None:
        counts = {
            row["state"]: row["count"]
            for row in self.db.execute("SELECT state, COUNT(*) AS count FROM items GROUP BY state")
        }
        kinds = {
            row["kind"]: row["count"]
            for row in self.db.execute("SELECT kind, COUNT(*) AS count FROM items WHERE state='done' GROUP BY kind")
        }
        summary = {
            "target_timestamp": self.args.timestamp,
            "hosts": self.args.host,
            "counts_by_state": counts,
            "completed_by_kind": kinds,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        (self.output / "mirror-report.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
        )

        with (self.output / "missing-resources.csv").open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["state", "http_status", "kind", "url", "error", "discovered_from"])
            for row in self.db.execute(
                """SELECT state, http_status, kind, url, error, discovered_from FROM items
                   WHERE state IN ('missing','failed') ORDER BY state, url"""
            ):
                writer.writerow(row)
        print(json.dumps(summary, indent=2), flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("actionbutton-site"))
    parser.add_argument("--timestamp", default="20230206182305")
    parser.add_argument(
        "--host", action="append", default=[],
        help="Original host to include (repeatable; defaults to www and bare actionbutton.net)",
    )
    parser.add_argument(
        "--seed", action="append", default=[],
        help="Original URL to seed (repeatable)",
    )
    parser.add_argument("--delay", type=float, default=0.35, help="Minimum seconds between requests")
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--backoff", type=float, default=1.5)
    parser.add_argument("--connect-timeout", type=float, default=15.0)
    parser.add_argument("--read-timeout", type=float, default=90.0)
    parser.add_argument("--max-items", type=int, default=0, help="Stop after N queue items; 0 means unlimited")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument(
        "--post-capture-index", type=Path,
        help="CDX JSON inventory used to recover posts whose target snapshot is a 404",
    )
    parser.add_argument(
        "--capture-index", action="append", type=Path, default=[],
        help="CDX JSON inventory used to recover any missing resource (repeatable)",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not args.host:
        args.host = ["www.actionbutton.net", "actionbutton.net"]
    if not args.seed:
        args.seed = [
            "http://www.actionbutton.net/?page_id=175",
            "http://www.actionbutton.net/",
            "http://www.actionbutton.net/?page_id=43",
            "http://www.actionbutton.net/?p=499",
        ]
    crawler = MirrorCrawler(args)
    try:
        crawler.run()
    except KeyboardInterrupt:
        print("Interrupted; progress is saved and the command can be resumed.", file=sys.stderr)
        crawler.write_reports()
        return 130
    finally:
        crawler.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
