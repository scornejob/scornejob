#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timezone
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


def _graphql_page_request(query: str, variables: dict[str, Any], gh_token: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    data = _graphql_request(query, variables, gh_token)
    user = data.get("user", {})
    repos = user.get("repositoriesContributedTo", {})
    nodes = repos.get("nodes", [])
    page_info = repos.get("pageInfo", {})
    return nodes, page_info


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


def _github_commit_search_count(username: str, gh_token: str | None) -> int:
    query = urllib.parse.quote(f"author:{username}")
    headers = _github_headers(gh_token)
    headers["Accept"] = "application/vnd.github.cloak-preview+json"
    url = f"https://api.github.com/search/commits?q={query}&per_page=1"
    data = _request_json(url, headers)
    return int(data.get("total_count", 0))


def _graphql_user_id(username: str, gh_token: str | None) -> str | None:
        if not gh_token:
                return None
        query = """
        query($login: String!) {
            user(login: $login) {
                id
            }
        }
        """
        data = _graphql_request(query, {"login": username}, gh_token)
        return data.get("user", {}).get("id")


def _graphql_repositories_contributed(username: str, gh_token: str | None, max_repos: int = 20) -> list[dict[str, Any]]:
    if not gh_token:
        return []

    query = """
    query($login: String!, $after: String) {
      user(login: $login) {
        repositoriesContributedTo(
          orderBy: {field: CREATED_AT, direction: DESC}
          first: 100
          after: $after
          includeUserRepositories: true
        ) {
          nodes {
            name
            isFork
            isPrivate
            owner { login }
            primaryLanguage { name }
            defaultBranchRef { name }
          }
          pageInfo {
            hasNextPage
            endCursor
          }
        }
      }
    }
    """

    all_repos: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    cursor: str | None = None

    while len(all_repos) < max_repos:
        nodes, page = _graphql_page_request(query, {"login": username, "after": cursor}, gh_token)
        for repo in nodes:
            if not repo or repo.get("isFork"):
                continue
            owner = repo.get("owner", {}).get("login")
            name = repo.get("name")
            if not owner or not name:
                continue
            key = (owner, name)
            if key in seen:
                continue
            seen.add(key)
            all_repos.append(repo)
            if len(all_repos) >= max_repos:
                break

        if not page.get("hasNextPage"):
            break
        cursor = page.get("endCursor")
        if not cursor:
            break

    return all_repos


def _graphql_branch_commits(
    repo_owner: str,
    repo_name: str,
    branch_name: str,
    author_id: str,
    gh_token: str | None,
    max_commits: int = 20,
) -> list[dict[str, Any]]:
    if not gh_token:
        return []

    query = """
    query($owner: String!, $name: String!, $qualified: String!, $authorId: ID!, $after: String) {
      repository(owner: $owner, name: $name) {
        ref(qualifiedName: $qualified) {
          target {
            ... on Commit {
              history(first: 100, after: $after, author: {id: $authorId}) {
                nodes {
                  oid
                  committedDate
                  additions
                  deletions
                }
                pageInfo {
                  hasNextPage
                  endCursor
                }
              }
            }
          }
        }
      }
    }
    """

    commits: list[dict[str, Any]] = []
    seen_oids: set[str] = set()
    cursor: str | None = None

    while len(commits) < max_commits:
        data = _graphql_request(
            query,
            {
                "owner": repo_owner,
                "name": repo_name,
                "qualified": f"refs/heads/{branch_name}",
                "authorId": author_id,
                "after": cursor,
            },
            gh_token,
        )
        history = (
            data.get("repository", {})
            .get("ref", {})
            .get("target", {})
            .get("history", {})
        )
        nodes = history.get("nodes", [])
        if not nodes:
            break

        for node in nodes:
            oid = node.get("oid")
            if not oid or oid in seen_oids:
                continue
            seen_oids.add(oid)
            commits.append(node)
            if len(commits) >= max_commits:
                break

        page = history.get("pageInfo", {})
        if not page.get("hasNextPage"):
            break
        cursor = page.get("endCursor")
        if not cursor:
            break

    return commits


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


def _format_human_loc(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f} million"
    if value >= 1_000:
        return f"{value:,}"
    return str(value)


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


def _commit_time_buckets(committed_dates: list[str], timezone_name: str | None) -> list[tuple[str, int]]:
    buckets = {
        "🌞 Morning": 0,
        "🌆 Daytime": 0,
        "🌃 Evening": 0,
        "🌙 Night": 0,
    }
    for ts in committed_dates:
        if not ts:
            continue
        dt_utc = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if timezone_name:
            try:
                from zoneinfo import ZoneInfo

                dt = dt_utc.astimezone(ZoneInfo(timezone_name))
            except Exception:
                dt = dt_utc.astimezone(UTC)
        else:
            dt = dt_utc.astimezone(UTC)
        hour = dt.hour
        if 6 <= hour < 12:
            buckets["🌞 Morning"] += 1
        elif 12 <= hour < 18:
            buckets["🌆 Daytime"] += 1
        elif 18 <= hour < 24:
            buckets["🌃 Evening"] += 1
        else:
            buckets["🌙 Night"] += 1
    return list(buckets.items())


def _commit_weekday_buckets(committed_dates: list[str], timezone_name: str | None) -> list[tuple[str, int]]:
    day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    days = {day: 0 for day in day_order}
    for ts in committed_dates:
        if not ts:
            continue
        dt_utc = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if timezone_name:
            try:
                from zoneinfo import ZoneInfo

                dt = dt_utc.astimezone(ZoneInfo(timezone_name))
            except Exception:
                dt = dt_utc.astimezone(UTC)
        else:
            dt = dt_utc.astimezone(UTC)
        days[dt.strftime("%A")] += 1
    return [(day, days[day]) for day in day_order]


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


def _format_repo_language_counts(language_counts: Counter[str], total_repos_with_language: int) -> str:
    if not language_counts or total_repos_with_language <= 0:
        return "No repositories found."

    lines: list[str] = []
    for name, count in language_counts.most_common(5):
        pct = _percent(count, total_repos_with_language)
        lines.append(
            f"{name:<24} {count:>10} repos         {_bar(pct)}   {pct:05.2f} % "
        )
    return "\n".join(lines)


def build_waka_block(api_key: str, username: str, gh_token: str | None) -> str:
    author_id = _graphql_user_id(username, gh_token)
    contributed_repos = _graphql_repositories_contributed(username, gh_token)

    committed_dates: list[str] = []
    total_loc = 0
    if author_id and gh_token:
        for repo in contributed_repos:
            owner = repo.get("owner", {}).get("login")
            name = repo.get("name")
            if not owner or not name:
                continue

            default_branch = repo.get("defaultBranchRef", {}).get("name")
            branch_names = [default_branch] if default_branch else []
            if not branch_names:
                continue

            for branch_name in branch_names:
                try:
                    commits = _graphql_branch_commits(owner, name, branch_name, author_id, gh_token)
                except Exception:
                    continue
                for commit in commits:
                    committed_date = commit.get("committedDate")
                    if committed_date:
                        committed_dates.append(committed_date)
                    total_loc += int(commit.get("additions") or 0)

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {
            "all_time": executor.submit(_wakatime_all_time, api_key),
            "last_7": executor.submit(_wakatime_last_7_days, api_key),
            "today": executor.submit(_wakatime_today, api_key),
            "github_user": executor.submit(_github_user, username, gh_token),
            "public_repos": executor.submit(_github_repos, username, gh_token, "public"),
            "private_repos": executor.submit(_github_repos, username, gh_token, "private"),
            "commits": executor.submit(_github_commit_search_count, username, gh_token),
            "year_contrib": executor.submit(_graph_year_contrib, username, gh_token),
        }

        all_time = futures["all_time"].result()
        last_7 = futures["last_7"].result()
        today = futures["today"].result()
        github_user = futures["github_user"].result()
        public_repos = futures["public_repos"].result()
        private_repos = futures["private_repos"].result()
        total_commits = futures["commits"].result()
        total_contrib_year = futures["year_contrib"].result()

    all_repos = public_repos + private_repos
    repo_languages = Counter(
        repo.get("primaryLanguage", {}).get("name")
        for repo in contributed_repos
        if repo.get("primaryLanguage") and repo.get("primaryLanguage", {}).get("name")
    )
    top_language = repo_languages.most_common(1)[0][0] if repo_languages else "Python"
    week = last_7.get("data", {})
    week_languages = week.get("languages", [])
    week_editors = week.get("editors", [])
    week_os = week.get("operating_systems", [])

    code_time_human = all_time.get("data", {}).get("text", "0 hrs 0 mins")
    profile_views = github_user.get("followers", 0)
    total_public = github_user.get("public_repos", 0)
    total_private = len(private_repos)
    hireable = github_user.get("hireable")
    hireable_text = "Opted to Hire" if hireable else "Not Opted to Hire"
    used_storage_kb = sum(int(repo.get("size", 0)) for repo in all_repos)
    updated = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M:%S UTC")
    current_year = datetime.now(timezone.utc).year
    timezone_name = week.get("timezone")

    time_of_day = _commit_time_buckets(committed_dates, timezone_name)
    weekday_counts = _commit_weekday_buckets(committed_dates, timezone_name)
    productive_day = _choose_most_productive(weekday_counts)

    primary_lang_lines = _format_repo_language_counts(repo_languages, sum(repo_languages.values()))
    editors_block = _format_languages_section(week_editors)
    os_block = _format_languages_section(week_os)
    week_lang_block = _format_languages_section(week_languages)
    time_of_day_block = _code_block_lines(time_of_day)
    weekday_block = _code_block_lines(weekday_counts)

    block = f"""
![Code Time](http://img.shields.io/badge/Code%20Time-{urllib.parse.quote(code_time_human)}-blue?style=flat)

![Profile Views](http://img.shields.io/badge/Profile%20Views-{profile_views}-blue?style=flat)

![Lines of code](https://img.shields.io/badge/From%20Hello%20World%20I%27ve%20Written-{urllib.parse.quote(_format_human_loc(total_loc))}%20lines%20of%20code%20in%20{urllib.parse.quote(_format_int(total_commits))}%20commits-blue?style=flat)

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
🕑︎ Time Zone: {timezone_name or 'UTC'}

💬 Programming Languages:
{week_lang_block}

🔥 Editors:
{editors_block}

💻 Operating System:
{os_block}
```

**I Mostly Code in {top_language}**

```text
{primary_lang_lines}
```

**Timeline**

![Lines of Code chart](https://raw.githubusercontent.com/scornejob/scornejob/master/assets/bar_graph.png)

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
