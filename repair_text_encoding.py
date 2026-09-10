#!/usr/bin/env python3
"""Repair UTF-8 text that was mistakenly decoded as Windows-1252.

The archived Action Button HTML contains strings such as ``didnâ€™t`` and
entity-encoded variants such as ``didn&acirc;&euro;&trade;t``.  This tool repairs
only character runs whose Windows-1252 byte values form a valid UTF-8 sequence,
so normal punctuation and already-correct Unicode are left alone.
"""

from __future__ import annotations

import argparse
import json
import warnings
from pathlib import Path

from bs4 import BeautifulSoup, Comment, Doctype, NavigableString, XMLParsedAsHTMLWarning


HTML_PATTERNS = ("*.html", "*.htm")
PLAIN_PATTERNS = ("*.css", "*.js", "*.xml")
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


def cp1252ish_byte(character: str) -> int | None:
    """Return the byte represented by a mojibake character, if unambiguous."""
    try:
        encoded = character.encode("cp1252")
        if len(encoded) == 1:
            return encoded[0]
    except UnicodeEncodeError:
        pass
    codepoint = ord(character)
    # Undefined Windows-1252 bytes often survive as C1 control characters.
    if 0x80 <= codepoint <= 0x9F:
        return codepoint
    return None


def sequence_length(first_byte: int | None) -> int:
    if first_byte is None:
        return 0
    if 0xC2 <= first_byte <= 0xDF:
        return 2
    if 0xE0 <= first_byte <= 0xEF:
        return 3
    if 0xF0 <= first_byte <= 0xF4:
        return 4
    return 0


def fix_one_pass(text: str) -> tuple[str, int]:
    output: list[str] = []
    replacements = 0
    index = 0
    while index < len(text):
        first = cp1252ish_byte(text[index])
        length = sequence_length(first)
        if length and index + length <= len(text):
            values = [cp1252ish_byte(char) for char in text[index:index + length]]
            if all(value is not None for value in values):
                raw = bytes(values)  # type: ignore[arg-type]
                try:
                    decoded = raw.decode("utf-8")
                except UnicodeDecodeError:
                    decoded = ""
                if decoded:
                    output.append(decoded)
                    replacements += 1
                    index += length
                    continue
        output.append(text[index])
        index += 1
    return "".join(output), replacements


def fix_text(text: str) -> tuple[str, int]:
    total = 0
    for _ in range(4):
        revised, count = fix_one_pass(text)
        total += count
        if not count or revised == text:
            return revised, total
        text = revised
    return text, total


def repair_html(source: str) -> tuple[str, int]:
    soup = BeautifulSoup(source, "html.parser")
    replacements = 0
    for node in list(soup.find_all(string=True)):
        if isinstance(node, (Comment, Doctype)):
            continue
        revised, count = fix_text(str(node))
        if count:
            node.replace_with(NavigableString(revised))
            replacements += count
    for tag in soup.find_all(True):
        for attribute, value in list(tag.attrs.items()):
            if isinstance(value, list):
                revised_values = []
                for item in value:
                    revised, count = fix_text(str(item))
                    revised_values.append(revised)
                    replacements += count
                tag[attribute] = revised_values
            else:
                revised, count = fix_text(str(value))
                if count:
                    tag[attribute] = revised
                    replacements += count
    if not replacements:
        return source, 0
    return soup.decode(formatter="html"), replacements


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("actionbutton-site"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    root = args.output.resolve()

    changed_files: dict[str, int] = {}
    scanned = 0
    paths: list[tuple[Path, bool]] = []
    for pattern in HTML_PATTERNS:
        paths.extend((path, True) for path in root.rglob(pattern))
    for pattern in PLAIN_PATTERNS:
        paths.extend((path, False) for path in root.rglob(pattern))

    for path, is_html in sorted(set(paths)):
        scanned += 1
        source = path.read_text(encoding="utf-8", errors="replace")
        revised, count = repair_html(source) if is_html else fix_text(source)
        if count and revised != source:
            changed_files[path.relative_to(root).as_posix()] = count
            if not args.dry_run:
                path.write_text(revised, encoding="utf-8", newline="")

    remaining_files: dict[str, int] = {}
    if not args.dry_run:
        for path, is_html in sorted(set(paths)):
            source = path.read_text(encoding="utf-8", errors="replace")
            _revised, count = repair_html(source) if is_html else fix_text(source)
            if count:
                remaining_files[path.relative_to(root).as_posix()] = count

    report = {
        "scanned_files": scanned,
        "changed_file_count": len(changed_files),
        "repaired_sequence_count": sum(changed_files.values()),
        "changed_files": changed_files,
        "remaining_repairable_files": remaining_files,
        "dry_run": args.dry_run,
    }
    report_path = root / "text-encoding-audit.json"
    if not args.dry_run:
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "scanned_files": scanned,
        "changed_files": len(changed_files),
        "repaired_sequences": sum(changed_files.values()),
        "remaining_repairable_files": len(remaining_files),
        "report": None if args.dry_run else str(report_path),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
