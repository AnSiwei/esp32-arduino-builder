#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清理 GitHub Releases，只保留最新的两个版本。

免费账号的 Release 存储有软限制，历史 assets 会持续累积占用配额。
本脚本在每次成功发布后运行，执行三类清理：

1. libs-* release：只保留最新的 N 个（默认 2），删除更旧的
2. framework-latest release：只保留最新的 N 个 assets（默认 2），删除更旧的
   （platform.json 只指向最新一个，保留一个作为回退）
3. debug-run-* release：全部删除（诊断用，无保留价值）

用法：
    python3 tools/cleanup_releases.py --repo owner/repo --token <GITHUB_TOKEN> [--keep 2]
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

GITHUB_API = "https://api.github.com"


def api_request(url: str, token: str, method: str = "GET", data: dict | None = None):
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code} for {method} {url}: {detail}", file=sys.stderr)
        return None


def list_all_releases(repo: str, token: str):
    releases = []
    page = 1
    while True:
        url = f"{GITHUB_API}/repos/{repo}/releases?per_page=100&page={page}"
        batch = api_request(url, token)
        if not batch:
            break
        releases.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return releases


def list_release_assets(repo: str, release_id: int, token: str):
    url = f"{GITHUB_API}/repos/{repo}/releases/{release_id}/assets?per_page=100"
    return api_request(url, token) or []


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean up GitHub Releases, keeping only the latest versions.")
    parser.add_argument("--repo", required=True, help="owner/repo")
    parser.add_argument("--token", required=True, help="GitHub token")
    parser.add_argument("--keep", type=int, default=2, help="number of latest versions to keep (default 2)")
    args = parser.parse_args()

    if args.keep < 1:
        print("--keep must be >= 1", file=sys.stderr)
        return 1

    releases = list_all_releases(args.repo, args.token)
    if not releases:
        print("No releases found; nothing to clean.")
        return 0

    # Newest first
    releases.sort(key=lambda r: r.get("created_at", ""), reverse=True)

    # 1. libs-* releases: keep only the N most recent
    libs_releases = [r for r in releases if r.get("tag_name", "").startswith("libs-")]
    for r in libs_releases[args.keep:]:
        print(f"Deleting old libs release: {r['tag_name']}")
        api_request(f"{GITHUB_API}/repos/{args.repo}/releases/{r['id']}", args.token, method="DELETE")

    # 2. framework-latest release: keep only the N most recent assets
    fw_releases = [r for r in releases if r.get("tag_name") == "framework-latest"]
    for r in fw_releases:
        assets = list_release_assets(args.repo, r["id"], args.token)
        assets.sort(key=lambda a: a.get("created_at", ""), reverse=True)
        for a in assets[args.keep:]:
            print(f"Deleting old framework asset: {a['name']}")
            api_request(f"{GITHUB_API}/repos/{args.repo}/releases/assets/{a['id']}", args.token, method="DELETE")

    # 3. debug-run-* releases: delete all (diagnostic only)
    debug_releases = [r for r in releases if r.get("tag_name", "").startswith("debug-run-")]
    for r in debug_releases:
        print(f"Deleting debug release: {r['tag_name']}")
        api_request(f"{GITHUB_API}/repos/{args.repo}/releases/{r['id']}", args.token, method="DELETE")

    print("Cleanup complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())