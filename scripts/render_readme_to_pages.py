#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib import request


def render_markdown(readme_text: str, repository: str, token: str) -> str:
    payload = {
        "text": readme_text,
        "mode": "gfm",
        "context": repository,
    }

    req = request.Request(
        "https://api.github.com/markdown",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "scornejob-pages-renderer",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="POST",
    )

    with request.urlopen(req) as response:
        return response.read().decode("utf-8")


def build_html(rendered_markdown: str) -> str:
    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>scornejob profile content</title>
  <link rel=\"stylesheet\" href=\"https://cdnjs.cloudflare.com/ajax/libs/github-markdown-css/5.8.1/github-markdown.min.css\" />
  <style>
    body {{
      margin: 0;
      background: linear-gradient(180deg, #f6f8fa 0%, #eef2f7 100%);
    }}
    .markdown-body {{
      box-sizing: border-box;
      max-width: 980px;
      margin: 40px auto;
      padding: 45px;
      border: 1px solid #d0d7de;
      border-radius: 14px;
      background: #ffffff;
      box-shadow: 0 20px 60px rgba(31, 35, 40, 0.08);
    }}
    @media (max-width: 767px) {{
      .markdown-body {{
        margin: 0;
        padding: 22px;
        border: 0;
        border-radius: 0;
        box-shadow: none;
      }}
    }}
    @media (prefers-color-scheme: dark) {{
      body {{
        background: #0d1117;
      }}
      .markdown-body {{
        background: #0d1117;
        color: #c9d1d9;
        border-color: #30363d;
        box-shadow: none;
      }}
    }}
  </style>
</head>
<body>
  <article class=\"markdown-body\">{rendered_markdown}</article>
</body>
</html>
"""


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError("Missing GITHUB_TOKEN environment variable")

    repository = os.environ.get("GITHUB_REPOSITORY", "")
    readme = Path("README.md").read_text(encoding="utf-8")
    rendered = render_markdown(readme, repository, token)
    html = build_html(rendered)

    out_dir = Path("_site")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(html, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
