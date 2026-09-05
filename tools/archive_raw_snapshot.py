#!/usr/bin/env python3
"""Create an upload-safe diagnostic archive of lib-builder output.

The Gitea reverse proxy rejects a single multi-gigabyte multipart upload.  This
helper creates a fast-deflated ZIP, then splits it into independently uploadable
parts when necessary.  A manifest records checksums and reassembly instructions.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import zipfile
from pathlib import Path
from typing import Iterable, List, Tuple

DEFAULT_MAX_PART_BYTES = 800 * 1024 * 1024
CHUNK_BYTES = 16 * 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        while True:
            chunk = fp.read(CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def source_files(source_dir: Path) -> Iterable[Tuple[Path, str]]:
    for root, dirs, files in os.walk(source_dir):
        dirs.sort()
        files.sort()
        root_path = Path(root)
        for filename in files:
            path = root_path / filename
            if path.is_file():
                yield path, path.relative_to(source_dir).as_posix()


def create_zip(source_dir: Path, archive_path: Path) -> int:
    count = 0
    with zipfile.ZipFile(
        archive_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=1,
        allowZip64=True,
    ) as archive:
        for path, arcname in source_files(source_dir):
            archive.write(path, arcname)
            count += 1
    return count


def split_archive(archive_path: Path, max_part_bytes: int) -> List[Path]:
    parts: List[Path] = []
    with archive_path.open("rb") as source:
        index = 1
        while True:
            part_path = archive_path.with_name(
                "{0}.part{1:03d}".format(archive_path.name, index)
            )
            written = 0
            with part_path.open("wb") as target:
                while written < max_part_bytes:
                    chunk = source.read(min(CHUNK_BYTES, max_part_bytes - written))
                    if not chunk:
                        break
                    target.write(chunk)
                    written += len(chunk)
            if written == 0:
                part_path.unlink()
                break
            parts.append(part_path)
            index += 1
    return parts


def write_manifest(
    manifest_path: Path,
    archive_name: str,
    archive_size: int,
    archive_sha256: str,
    file_count: int,
    upload_parts: List[Path],
) -> None:
    lines = [
        "PioArduino raw lib-builder diagnostic snapshot",
        "archive_name={0}".format(archive_name),
        "archive_size_bytes={0}".format(archive_size),
        "archive_sha256={0}".format(archive_sha256),
        "compression=zip-deflate-level-1",
        "source_file_count={0}".format(file_count),
        "",
        "uploaded_files:",
    ]
    for part in upload_parts:
        lines.append(
            "  {0}\tsize={1}\tsha256={2}".format(
                part.name, part.stat().st_size, sha256_file(part)
            )
        )
    lines.append("")
    if len(upload_parts) == 1 and upload_parts[0].name == archive_name:
        lines.append("The uploaded ZIP is ready to inspect directly.")
    else:
        joined = " ".join(part.name for part in upload_parts)
        lines.extend(
            [
                "Reassemble after downloading every part:",
                "  Linux/macOS: cat {0} > {1}".format(joined, archive_name),
                "  Windows cmd: copy /b {0} {1}".format(
                    "+".join(part.name for part in upload_parts), archive_name
                ),
                "Verify the reassembled ZIP SHA-256 against archive_sha256 above.",
            ]
        )
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--asset-list", type=Path, required=True)
    parser.add_argument(
        "--basename", default="raw-esp32-arduino-libs.zip", help="ZIP filename"
    )
    parser.add_argument(
        "--max-part-bytes", type=int, default=DEFAULT_MAX_PART_BYTES,
        help="maximum size of each uploaded archive part",
    )
    args = parser.parse_args()

    if args.max_part_bytes <= 0:
        parser.error("--max-part-bytes must be positive")
    if not args.source_dir.is_dir():
        parser.error("--source-dir must be an existing directory")

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = output_dir / args.basename
    manifest_path = output_dir / (args.basename + ".manifest.txt")

    for stale in output_dir.glob(args.basename + ".part*"):
        stale.unlink()
    for stale in (archive_path, manifest_path, args.asset_list):
        if stale.exists():
            stale.unlink()

    file_count = create_zip(args.source_dir, archive_path)
    archive_size = archive_path.stat().st_size
    archive_sha256 = sha256_file(archive_path)

    if archive_size > args.max_part_bytes:
        upload_parts = split_archive(archive_path, args.max_part_bytes)
        archive_path.unlink()
    else:
        upload_parts = [archive_path]

    write_manifest(
        manifest_path,
        args.basename,
        archive_size,
        archive_sha256,
        file_count,
        upload_parts,
    )
    assets = upload_parts + [manifest_path]
    args.asset_list.write_text(
        "".join("{0}\n".format(path.as_posix()) for path in assets),
        encoding="utf-8",
    )

    print("raw snapshot archive size: {0} bytes".format(archive_size))
    print("raw snapshot upload assets: {0}".format(", ".join(path.name for path in assets)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
