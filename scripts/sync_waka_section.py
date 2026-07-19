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


def _graphql_request(query: str, variables: dict[str, Any], gh_token: str) -> Any:
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=payload,
        headers={
            "Authorization": f"Bearer {gh_token}",
            "Content-Type": "application/json",
            "Accept": "application/vnd.github+json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))
    if data.get("errors"):
        raise ValueError(f"GraphQL error: {data['errors'][0].get('message', 'unknown')}" )
    return data.get("data", {})


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


def _github_user_events(username: str, gh_token: str | None) -> list[dict[str, Any]]:
    headers = _github_headers(gh_token)
    events: list[dict[str, Any]] = []
    for page in range(1, 4):
        url = (
            f"https://api.github.com/users/{urllib.parse.quote(username)}/events"
            f"?per_page=100&page={page}"
        )
        batch = _request_json(url, headers)
        if not batch:
            break
        events.extend(batch)
        if len(batch) < 100:
            break
    return events


def _github_commit_search_count(username: str, gh_token: str | None) -> int:
    query = urllib.parse.quote(f"author:{username}")
    headers = _github_headers(gh_token)
    headers["Accept"] = "application/vnd.github.cloak-preview+json"
    url = f"https://api.github.com/search/commits?q={query}&per_page=1"
    data = _request_json(url, headers)
    return int(data.get("total_count", 0))


def _github_issue_pr_counts(username: str, gh_token: str | None) -> tuple[int, int]:
    headers = _github_headers(gh_token)
    issue_q = urllib.parse.quote(f"author:{username} is:issue")
    pr_q = urllib.parse.quote(f"author:{username} is:pr")
    issues = _request_json(f"https://api.github.com/search/issues?q={issue_q}&per_page=1", headers)
    prs = _request_json(f"https://api.github.com/search/issues?q={pr_q}&per_page=1", headers)
    return int(issues.get("total_count", 0)), int(prs.get("total_count", 0))


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


def _format_int(value: int) -> str:
    return f"{value:,}"


def _percent(value: float, total: float) -> float:
    if total <= 0:
        return 0.0
    return (value / total) * 100


def _bar(percent: float, width: int = 25) -> str:
    filled = max(0, min(width, int(round((percent / 100.0) * width))))
    return "█" * filled + "░" * (width - filled)


def _code_block_lines(items: list[tuple[str, int]]) -> str:
    total = sum(v for _, v in items)
    lines: list[str] = []
    for label, count in items:
        pct = _percent(count, total)
        lines.append(
            f"{label:<24} {_format_int(count):>10} commits       {_bar(pct)}   {pct:05.2f} % "
        )
    return "\n".join(lines)


def _graph_year_contrib(username: str, gh_token: str | None) -> int:
    if not gh_token:
        return 0
    query = """
    query($login: String!) {
      user(login: $login) {
        contributionsCollection {
          contributionCalendar {
            totalContributions
          }
        }
      }
    }
    """
    data = _graphql_request(query, {"login": username}, gh_token)
    return int(
        data.get("user", {})
        .get("contributionsCollection", {})
        .get("contributionCalendar", {})
        .get("totalContributions", 0)
    )


def _events_time_buckets(events: list[dict[str, Any]]) -> list[tuple[str, int]]:
    buckets = {
        "🌞 Morning": 0,
        "🌆 Daytime": 0,
        "🌃 Evening": 0,
        "🌙 Night": 0,
    }
    for event in events:
        ts = event.get("created_at")
        if not ts:
            continue
        hour = datetime.fromisoformat(ts.replace("Z", "+00:00")).hour
        if 6 <= hour < 12:
            buckets["🌞 Morning"] += 1
        elif 12 <= hour < 18:
            buckets["🌆 Daytime"] += 1
        elif 18 <= hour < 24:
            buckets["🌃 Evening"] += 1
        else:
            buckets["🌙 Night"] += 1
    return list(buckets.items())


def _events_weekday_buckets(events: list[dict[str, Any]]) -> list[tuple[str, int]]:
    days = {
        "Monday": 0,
        "Tuesday": 0,
        "Wednesday": 0,
        "Thursday": 0,
        "Friday": 0,
        "Saturday": 0,
        "Sunday": 0,
    }
    for event in events:
        ts = event.get("created_at")
        if not ts:
            continue
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        days[dt.strftime("%A")] += 1
    return list(days.items())


def _choose_most_productive(weekday_counts: list[tuple[str, int]]) -> str:
    if not weekday_counts:
        return "Monday"
    return max(weekday_counts, key=lambda t: t[1])[0]


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
        percent = float(item.get("percent", 0))
        lines.append(
            f"{name:<24} {text:<18} {_bar(percent)}   {percent:05.2f} % "
        )
    return "\n".join(lines)


def _format_top_repos(repos: list[dict[str, Any]]) -> str:
    if not repos:
        return "No repositories found."
    lines: list[str] = []
    top = sorted(repos, key=lambda r: r.get("stargazers_count", 0), reverse=True)[:5]
    total = sum(r.get("stargazers_count", 0) for r in top) or len(top)
    for repo in top:
        name = repo.get("language") or "Other"
        stars = int(repo.get("stargazers_count", 0))
        pct = _percent(stars if stars > 0 else 1, total)
        lines.append(
            f"{name:<24} {stars:>10} repos         {_bar(pct)}   {pct:05.2f} % "
        )
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
            "events": executor.submit(_github_user_events, username, gh_token),
            "commits": executor.submit(_github_commit_search_count, username, gh_token),
            "issues_prs": executor.submit(_github_issue_pr_counts, username, gh_token),
            "year_contrib": executor.submit(_graph_year_contrib, username, gh_token),
        }

        all_time = futures["all_time"].result()
        last_7 = futures["last_7"].result()
        today = futures["today"].result()
        github_user = futures["github_user"].result()
        public_repos = futures["public_repos"].result()
        private_repos = futures["private_repos"].result()
        events = futures["events"].result()
        total_commits = futures["commits"].result()
        total_issues, total_prs = futures["issues_prs"].result()
        total_contrib_year = futures["year_contrib"].result()

    all_repos = public_repos + private_repos
    primary_languages = _language_counts(all_repos)[:5]
    week = last_7.get("data", {})
    week_languages = week.get("languages", [])
    week_editors = week.get("editors", [])
    week_os = week.get("operating_systems", [])

    code_time_human = all_time.get("data", {}).get("text", "0 hrs 0 mins")
    today_text = today.get("data", {}).get("grand_total", {}).get("text") or "0 secs"
    profile_views = github_user.get("followers", 0)
    total_public = github_user.get("public_repos", 0)
    total_private = len(private_repos)
    hireable = github_user.get("hireable")
    hireable_text = "Opted to Hire" if hireable else "Not Opted to Hire"
    used_storage_kb = sum(int(repo.get("size", 0)) for repo in all_repos)
    updated = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M:%S UTC")
    current_year = datetime.now(timezone.utc).year

    time_of_day = _events_time_buckets(events)
    weekday_counts = _events_weekday_buckets(events)
    productive_day = _choose_most_productive(weekday_counts)

    primary_lang_lines = _format_top_repos(
        [{"language": lang, "stargazers_count": count} for lang, count in primary_languages]
    )
    editors_block = _format_languages_section(week_editors)
    os_block = _format_languages_section(week_os)
    week_lang_block = _format_languages_section(week_languages)
    time_of_day_block = _code_block_lines(time_of_day)
    weekday_block = _code_block_lines(weekday_counts)

    block = f"""
![Code Time](http://img.shields.io/badge/Code%20Time-{urllib.parse.quote(code_time_human)}-blue?style=flat)

![Profile Views](http://img.shields.io/badge/Profile%20Views-{profile_views}-blue?style=flat)

![Lines of code](https://img.shields.io/badge/From%20Hello%20World%20I%27ve%20Written-{urllib.parse.quote(_format_int(total_commits))}%20commits-blue?style=flat)

**🐱 My GitHub Data**

> 📦 {_format_int(used_storage_kb)} kB Used in GitHub's Storage
 >
> 🏆 {_format_int(total_contrib_year)} Contributions in the Year {current_year}
 >
> 🚫 {hireable_text}
 >
> 📜 {total_public} Public Repositories
>
> 🔑 {total_private} Private Repositories

**I'm an Early 🐤**

```text
{time_of_day_block}
```

📅 **I'm Most Productive on {productive_day}**

```text
{weekday_block}
```

📊 **This Week I Spent My Time On**

```text
🕑︎ Time Zone: Europe/Berlin

💬 Programming Languages:
{week_lang_block}

🔥 Editors:
{editors_block}

💻 Operating System:
{os_block}
```

**I Mostly Code in Python**

```text
{primary_lang_lines}
```

**Timeline**

![Lines of Code chart](https://raw.githubusercontent.com/scornejob/scornejob/master/assets/bar_graph.png)

I've opened {_format_int(total_issues)} issues throughout this time.

Also, I've contributed with {_format_int(total_prs)} pull requests.

I've made {_format_int(total_commits)} commits.

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
