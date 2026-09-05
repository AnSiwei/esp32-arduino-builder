#!/usr/bin/env python3
"""Normalize CI-only absolute picolibc specs paths in generated PioArduino metadata."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    from validate_pio_libs import DEFAULT_TARGETS, FLAG_NAMES
except ModuleNotFoundError:  # Support importing this file from the repository root.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from validate_pio_libs import DEFAULT_TARGETS, FLAG_NAMES

# lib-builder is expected to turn these into a toolchain-relative specs file,
# but its shell conversion can leave an absolute CI path behind.  Only rewrite
# picolibc specs paths; nano.specs is deliberately left untouched so the
# validator rejects the incompatible artifact.
ABSOLUTE_PICOLIBC_SPECS_RE = re.compile(
    r'''(?:--?specs=)(?:\\?["'])?(?:/(?:opt/esp|workspace|home/runner)/|[A-Za-z]:[\\/])[^\s"']*?'''
    r'''(?P<spec>picolibc(?:pp)?\.specs)'''
)


def normalize_file(path: Path) -> int:
    text = path.read_text(encoding="utf-8", errors="replace")
    updated, count = ABSOLUTE_PICOLIBC_SPECS_RE.subn(
        lambda match: f"-specs={match.group('spec')}", text
    )
    if count:
        path.write_text(updated, encoding="utf-8", newline="")
    return count


def metadata_files(chip_dir: Path) -> list[Path]:
    files: list[Path] = []
    script = chip_dir / "pioarduino-build.py"
    if script.is_file():
        files.append(script)
    flags = chip_dir / "flags"
    if flags.is_dir():
        files.extend(
            path for path in sorted(flags.iterdir())
            if path.is_file() and path.name in FLAG_NAMES
        )
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--libs-dir", required=True, type=Path)
    parser.add_argument("--targets", default=",".join(DEFAULT_TARGETS))
    args = parser.parse_args()

    targets = [item.strip() for item in args.targets.split(",") if item.strip()]
    if not targets:
        print("no targets supplied", file=sys.stderr)
        return 2

    changes = 0
    for chip in targets:
        chip_dir = args.libs_dir / chip
        for path in metadata_files(chip_dir):
            count = normalize_file(path)
            if count:
                changes += count
                print(f"normalized {count} specs path(s): {path}")
    print(f"normalized {changes} absolute picolibc specs path(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
