#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

START = "<!--START_SECTION:waka-->"
END = "<!--END_SECTION:waka-->"


def extract_waka_block(text: str) -> str:
    start = text.find(START)
    end = text.find(END)
    if start == -1 or end == -1 or end < start:
        raise ValueError("Could not find waka section markers in source file")
    return text[start + len(START):end]


def replace_waka_block(text: str, block: str) -> str:
    start = text.find(START)
    end = text.find(END)
    if start == -1 or end == -1 or end < start:
        raise ValueError("Could not find waka section markers in target file")
    return text[: start + len(START)] + block + text[end:]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Copy README to a target file and sync waka section from source"
    )
    parser.add_argument("--source", default="README.md", help="source README path")
    parser.add_argument("--target", default="README-TEST.md", help="target README path")
    args = parser.parse_args()

    source_path = Path(args.source)
    target_path = Path(args.target)

    source_text = source_path.read_text(encoding="utf-8")
    waka_block = extract_waka_block(source_text)

    # Start from a full copy, then explicitly replace waka section to guarantee parity.
    target_text = source_text
    target_text = replace_waka_block(target_text, waka_block)

    target_path.write_text(target_text, encoding="utf-8")

    print(f"Synced waka section from {source_path} to {target_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
