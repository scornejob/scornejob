#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

START = "<!--START_SECTION:waka-->"
END = "<!--END_SECTION:waka-->"


def _request_json(url: str, headers: dict[str, str] | None = None) -> Any:
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _wakatime_headers() -> dict[str, str]:
    return {"Accept": "application/json"}


def _github_headers(gh_token: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"
    return headers


def _wakatime_all_time(api_key: str) -> Any:
    url = "https://wakatime.com/api/v1/users/current/all_time_since_today"
    url = f"{url}?api_key={urllib.parse.quote(api_key)}"
    return _request_json(url, _wakatime_headers())


def _wakatime_last_7_days(api_key: str) -> Any:
    url = "https://wakatime.com/api/v1/users/current/stats/last_7_days"
    url = f"{url}?api_key={urllib.parse.quote(api_key)}"
    return _request_json(url, _wakatime_headers())


def _wakatime_today(api_key: str) -> Any:
    url = "https://wakatime.com/api/v1/users/current/status_bar/today"
    url = f"{url}?api_key={urllib.parse.quote(api_key)}"
    return _request_json(url, _wakatime_headers())


def _github_user(username: str, gh_token: str | None) -> Any:
    url = f"https://api.github.com/users/{urllib.parse.quote(username)}"
    return _request_json(url, _github_headers(gh_token))


def _github_repos(username: str, gh_token: str | None, visibility: str = "public") -> list[dict[str, Any]]:
    headers = _github_headers(gh_token)
    repos: list[dict[str, Any]] = []
    page = 1
    while True:
        url = (
            "https://api.github.com/user/repos"
            if gh_token
            else f"https://api.github.com/users/{urllib.parse.quote(username)}/repos"
        )
        query = {
            "per_page": "100",
            "page": str(page),
            "sort": "updated",
            "direction": "desc",
        }
        if gh_token:
            query["visibility"] = visibility
            query["affiliation"] = "owner"
        else:
            query["type"] = "owner"
        response = _request_json(f"{url}?{urllib.parse.urlencode(query)}", headers)
        if not response:
            break
        repos.extend(response)
        if len(response) < 100:
            break
        page += 1
    return repos


def _language_counts(repos: list[dict[str, Any]]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for repo in repos:
        lang = repo.get("language")
        if not lang:
            continue
        counts[lang] = counts.get(lang, 0) + 1
    return sorted(counts.items(), key=lambda item: item[1], reverse=True)


def replace_waka_block(text: str, block: str) -> str:
    start = text.find(START)
    end = text.find(END)
    if start == -1 or end == -1 or end < start:
        raise ValueError("Could not find waka section markers in target file")
    return text[: start + len(START)] + block + text[end:]


def _format_languages_section(languages: list[dict[str, Any]]) -> str:
    if not languages:
        return "No language activity found for the selected period."
    lines: list[str] = []
    for item in languages[:8]:
        name = item.get("name", "Unknown")
        text = item.get("text", "0 secs")
        percent = item.get("percent", 0)
        lines.append(f"- {name}: {text} ({percent}%)")
    return "\n".join(lines)


def _format_top_repos(repos: list[dict[str, Any]]) -> str:
    if not repos:
        return "No repositories found."
    lines: list[str] = []
    for repo in repos[:5]:
        name = repo.get("name", "unknown")
        stars = repo.get("stargazers_count", 0)
        lines.append(f"- {name} ({stars}★)")
    return "\n".join(lines)


def build_waka_block(api_key: str, username: str, gh_token: str | None) -> str:
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {
            "all_time": executor.submit(_wakatime_all_time, api_key),
            "last_7": executor.submit(_wakatime_last_7_days, api_key),
            "today": executor.submit(_wakatime_today, api_key),
            "github_user": executor.submit(_github_user, username, gh_token),
            "public_repos": executor.submit(_github_repos, username, gh_token, "public"),
            "private_repos": executor.submit(_github_repos, username, gh_token, "private"),
        }

        all_time = futures["all_time"].result()
        last_7 = futures["last_7"].result()
        today = futures["today"].result()
        github_user = futures["github_user"].result()
        public_repos = futures["public_repos"].result()
        private_repos = futures["private_repos"].result()

    all_repos = public_repos + private_repos
    primary_languages = _language_counts(all_repos)[:5]
    week = last_7.get("data", {})
    week_languages = week.get("languages", [])

    code_time_human = all_time.get("data", {}).get("text", "0 hrs 0 mins")
    today_text = today.get("data", {}).get("grand_total", {}).get("text", "0 hrs 0 mins")
    profile_views = github_user.get("followers", 0)
    total_public = github_user.get("public_repos", 0)
    total_private = len(private_repos)
    updated = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M:%S UTC")

    primary_lang_lines = "\n".join(
        f"- {lang}: {count} repos" for lang, count in primary_languages
    ) or "No repository language data available."

    block = f"""
![Code Time](http://img.shields.io/badge/Code%20Time-{urllib.parse.quote(code_time_human)}-blue?style=flat)

![Profile Views](http://img.shields.io/badge/Profile%20Views-{profile_views}-blue?style=flat)

**🐱 My GitHub Data**

> 📜 {total_public} Public Repositories
>
> 🔑 {total_private} Private Repositories
>
> 🕒 Today I coded for {today_text}

📊 **This Week I Spent My Time On**

{_format_languages_section(week_languages)}

**I Mostly Code In**

{primary_lang_lines}

**Recently Updated Repos**

{_format_top_repos(all_repos)}

 Last Updated on {updated}
"""
    return block


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Populate target waka section from WakaTime and GitHub APIs"
    )
    parser.add_argument("--target", default="README-TEST.md", help="target README path")
    parser.add_argument(
        "--username",
        default=os.getenv("GITHUB_USERNAME"),
        help="GitHub username (defaults to GITHUB_USERNAME env)",
    )
    args = parser.parse_args()

    target_path = Path(args.target)
    api_key = os.getenv("WAKATIME_API_KEY")
    gh_token = os.getenv("GH_TOKEN")
    username = args.username

    if not api_key:
        raise ValueError("Missing WAKATIME_API_KEY environment variable")
    if not username:
        raise ValueError("Missing GitHub username. Set --username or GITHUB_USERNAME")

    target_text = target_path.read_text(encoding="utf-8")
    waka_block = build_waka_block(api_key, username, gh_token)
    target_text = replace_waka_block(target_text, f"\n{waka_block}\n")

    target_path.write_text(target_text, encoding="utf-8")

    print(f"Populated waka section in {target_path} using API data")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
