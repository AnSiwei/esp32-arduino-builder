#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GitHub Release 上传/下载/删除辅助工具。

拆分构建后，各芯片 job 把产物上传到 debug-run release 作为中间存储，
合并 job 再下载。本脚本封装 GitHub Release API 的常用操作。

用法：
    # 上传单个文件（同名已存在则先删除）
    python3 tools/release_io.py upload --repo owner/repo --token TOKEN \
        --tag debug-run-123 --file path/to/file.tar.gz

    # 下载 release 下所有匹配前缀的 asset 到目录
    python3 tools/release_io.py download --repo owner/repo --token TOKEN \
        --tag debug-run-123 --prefix libs- --out-dir ./downloads

    # 删除 release 下所有匹配前缀的 asset
    python3 tools/release_io.py delete-assets --repo owner/repo --token TOKEN \
        --tag debug-run-123 --prefix libs-
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

GITHUB_API = "https://api.github.com"
# 上传 release asset 必须使用 uploads.github.com 域名（api.github.com 会返回 404）。
# 参考 GitHub 文档：Upload a release asset 端点使用 Hypermedia 关系，
# 实际 URL 是 https://uploads.github.com/repos/{owner}/{repo}/releases/{release_id}/assets
GITHUB_UPLOADS_API = "https://uploads.github.com"


def api(url: str, token: str, method: str = "GET", data: dict | None = None,
        raw_body: bytes | None = None, content_type: str | None = None):
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
    body = None
    if raw_body is not None:
        body = raw_body
        if content_type:
            headers["Content-Type"] = content_type
    elif data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return json.loads(raw.decode("utf-8")) if raw else None
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code} for {method} {url}: {detail}", file=sys.stderr)
        return None


def get_release(repo: str, token: str, tag: str):
    return api(f"{GITHUB_API}/repos/{repo}/releases/tags/{tag}", token)


def create_release(repo: str, token: str, tag: str, name: str, body: str,
                   prerelease: bool = True):
    return api(
        f"{GITHUB_API}/repos/{repo}/releases", token, method="POST",
        data={
            "tag_name": tag,
            "target_commitish": "develop",
            "name": name,
            "body": body,
            "draft": False,
            "prerelease": prerelease,
        },
    )


def ensure_release(repo: str, token: str, tag: str, name: str, body: str,
                   prerelease: bool = True):
    rel = get_release(repo, token, tag)
    if rel and rel.get("id"):
        return rel
    # 多个芯片 job 并行上传到同一 debug-run release，可能同时发现 release
    # 不存在并尝试创建。创建失败（tag 已存在）时重试 GET 获取已创建的 release。
    created = create_release(repo, token, tag, name, body, prerelease)
    if created and created.get("id"):
        return created
    rel = get_release(repo, token, tag)
    if rel and rel.get("id"):
        return rel
    return created


def list_assets(repo: str, token: str, release_id: int):
    return api(f"{GITHUB_API}/repos/{repo}/releases/{release_id}/assets?per_page=100", token) or []


def upload_asset(repo: str, token: str, release_id: int, path: str):
    name = os.path.basename(path)
    # 同名先删除
    for a in list_assets(repo, token, release_id):
        if a.get("name") == name:
            api(f"{GITHUB_API}/repos/{repo}/releases/assets/{a['id']}", token, method="DELETE")
    with open(path, "rb") as fp:
        data = fp.read()
    # 必须用 uploads.github.com 域名，否则 404
    url = f"{GITHUB_UPLOADS_API}/repos/{repo}/releases/{release_id}/assets?name={name}"
    return api(url, token, method="POST", raw_body=data, content_type="application/octet-stream")


def download_asset(repo: str, token: str, asset: dict, out_dir: str):
    url = asset["browser_download_url"]
    req = urllib.request.Request(url, headers={"Authorization": f"token {token}"})
    with urllib.request.urlopen(req) as resp:
        data = resp.read()
    out_path = os.path.join(out_dir, asset["name"])
    with open(out_path, "wb") as fp:
        fp.write(data)
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", required=True)
    common.add_argument("--token", required=True)
    common.add_argument("--tag", required=True)

    p_upload = sub.add_parser("upload", parents=[common])
    p_upload.add_argument("--file", required=True)

    p_dl = sub.add_parser("download", parents=[common])
    p_dl.add_argument("--prefix", default="")
    p_dl.add_argument("--out-dir", required=True)

    p_del = sub.add_parser("delete-assets", parents=[common])
    p_del.add_argument("--prefix", default="")

    args = parser.parse_args()

    rel = ensure_release(args.repo, args.token, args.tag,
                         f"Build snapshot {args.tag}",
                         "Intermediate build artifacts. Diagnostic only.",
                         prerelease=True)
    if not rel or not rel.get("id"):
        print("Could not obtain release", file=sys.stderr)
        return 1
    release_id = rel["id"]

    if args.command == "upload":
        result = upload_asset(args.repo, args.token, release_id, args.file)
        if not result or result.get("name") != os.path.basename(args.file):
            print("Upload failed", file=sys.stderr)
            return 1
        print(f"Uploaded: {result['name']}")
        return 0

    if args.command == "download":
        os.makedirs(args.out_dir, exist_ok=True)
        assets = [a for a in list_assets(args.repo, args.token, release_id)
                  if a.get("name", "").startswith(args.prefix)]
        if not assets:
            print(f"No assets with prefix {args.prefix!r}", file=sys.stderr)
            return 1
        for a in assets:
            path = download_asset(args.repo, args.token, a, args.out_dir)
            print(f"Downloaded: {path}")
        return 0

    if args.command == "delete-assets":
        assets = [a for a in list_assets(args.repo, args.token, release_id)
                  if a.get("name", "").startswith(args.prefix)]
        for a in assets:
            api(f"{GITHUB_API}/repos/{args.repo}/releases/assets/{a['id']}",
                args.token, method="DELETE")
            print(f"Deleted: {a['name']}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())