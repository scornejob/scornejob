#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path
from typing import Dict
from urllib import parse, request

ISSUES_RE = re.compile(r"I've opened\s+(\d+)\s+issues", re.IGNORECASE)
PRS_RE = re.compile(r"I've contributed with\s+(\d+)\s+pull requests", re.IGNORECASE)
COMMITS_RE = re.compile(r"I've made\s+(\d+)\s+commits", re.IGNORECASE)
CONTRIB_REPOS_RE = re.compile(r"distributed amongst\s+(\d+)\s+repos", re.IGNORECASE)


def extract_current_stats(text: str) -> Dict[str, int]:
    def get(regex: re.Pattern[str]) -> int:
        m = regex.search(text)
        return int(m.group(1)) if m else 0

    return {
        "ISSUES": get(ISSUES_RE),
        "PULL_REQUESTS": get(PRS_RE),
        "COMMITS": get(COMMITS_RE),
        "REPOSITORIES_CONTRIBUTED_TO": get(CONTRIB_REPOS_RE),
    }


def github_get(url: str, token: str) -> dict:
    req = request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "scornejob-readme-test-generator",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="GET",
    )
    with request.urlopen(req) as res:
        return json.loads(res.read().decode("utf-8"))


def with_retry(url: str, token: str, fallback: int) -> int:
    last_error = None
    for attempt in range(1, 4):
        try:
            data = github_get(url, token)
            return int(data.get("total_count", fallback))
        except Exception as error:  # noqa: BLE001
            last_error = error
            print(f"Request attempt {attempt}/3 failed for {url}: {error}")
            if attempt < 3:
                time.sleep(attempt)
    print(f"Falling back for {url}: {last_error}")
    return fallback


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate README-like file from template")
    parser.add_argument("--template", default="TEMPLATE.md")
    parser.add_argument("--output", default="README-TEST.md")
    parser.add_argument("--token", default=os.environ.get("STATS_TOKEN") or os.environ.get("GITHUB_TOKEN", ""))
    args = parser.parse_args()

    template_text = Path(args.template).read_text(encoding="utf-8")
    existing_output_text = Path(args.output).read_text(encoding="utf-8") if Path(args.output).exists() else ""
    current = extract_current_stats(existing_output_text)

    owner = (os.environ.get("GITHUB_REPOSITORY") or "").split("/")[0]
    if not owner:
        raise RuntimeError("GITHUB_REPOSITORY is required")

    replacements = dict(current)
    if args.token:
        issues_q = parse.quote(f"author:{owner} is:issue", safe="")
        prs_q = parse.quote(f"author:{owner} is:pr", safe="")
        commits_q = parse.quote(f"author:{owner}", safe="")

        replacements["ISSUES"] = with_retry(
            f"https://api.github.com/search/issues?q={issues_q}&per_page=1",
            args.token,
            current["ISSUES"],
        )
        replacements["PULL_REQUESTS"] = with_retry(
            f"https://api.github.com/search/issues?q={prs_q}&per_page=1",
            args.token,
            current["PULL_REQUESTS"],
        )
        replacements["COMMITS"] = with_retry(
            f"https://api.github.com/search/commits?q={commits_q}&per_page=1",
            args.token,
            current["COMMITS"],
        )

    output = re.sub(
        r"\{\{\s*(ISSUES|PULL_REQUESTS|COMMITS|REPOSITORIES_CONTRIBUTED_TO)\s*\}\}",
        lambda m: str(replacements[m.group(1)]),
        template_text,
    )
    Path(args.output).write_text(output, encoding="utf-8")
    print(f"Generated {args.output} from {args.template}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
