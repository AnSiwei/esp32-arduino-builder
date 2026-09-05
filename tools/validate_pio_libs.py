#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate a PioArduino static-library package before publishing it."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_TARGETS = (
    "esp32",
    "esp32s2",
    "esp32s3",
    "esp32c2",
    "esp32c3",
    "esp32c5",
    "esp32c6",
    "esp32c61",
    "esp32h2",
    "esp32p4",
)

# These files are consumed by PlatformIO.  Keep the validation focused on
# actual build metadata: pioarduino-build.py also contains harmless HTTP URLs.
FLAG_NAMES = {"c_flags", "cpp_flags", "cxx_flags", "ld_flags", "ld_libs"}
# Absolute paths are valid in some generated response-file metadata.  A path
# embedded in -specs/--specs is different: GCC resolves it at build time, so
# publishing it would make the package depend on the CI worker filesystem.
ABSOLUTE_SPECS_RE = re.compile(
    r'''(?:^|[\s"'=])--?specs=["']?(?:/(?:opt/esp|workspace|home/runner)|[A-Za-z]:[\\/])'''
)


def read_metadata(chip_dir: Path) -> tuple[str, str]:
    """Return (framework script, generated compiler/linker flags)."""
    script = chip_dir / "pioarduino-build.py"
    script_text = (
        script.read_text(encoding="utf-8", errors="replace")
        if script.is_file()
        else ""
    )
    flag_chunks: list[str] = []
    flags = chip_dir / "flags"
    if flags.is_dir():
        for path in sorted(flags.iterdir()):
            if path.is_file() and path.name in FLAG_NAMES:
                flag_chunks.append(path.read_text(encoding="utf-8", errors="replace"))
    return script_text, "\n".join(flag_chunks)


def validate(libs_dir: Path, targets: list[str]) -> list[str]:
    errors: list[str] = []
    for chip in targets:
        chip_dir = libs_dir / chip
        lib_dir = chip_dir / "lib"
        if not lib_dir.is_dir() or not any(lib_dir.rglob("*.a")):
            errors.append(f"{chip}: missing lib/*.a")
            continue

        script_metadata, flag_metadata = read_metadata(chip_dir)
        metadata = "\n".join(part for part in (script_metadata, flag_metadata) if part)
        # ESP32/ESP32-S2 use the legacy Xtensa layout and may not have per-chip
        # PioArduino metadata. Any chip that does have metadata must be complete.
        if not metadata:
            continue

        # ESP-IDF's component is still named newlib even when CONFIG_LIBC_PICOLIBC
        # selects picolibc internally.  Therefore -lnewlib is expected and is not
        # evidence of the old incompatible artifact by itself.
        if "picolibc.specs" not in metadata:
            errors.append(f"{chip}: missing picolibc.specs in PioArduino metadata")
        if "nano.specs" in metadata:
            errors.append(f"{chip}: metadata still uses nano.specs")
        # pioarduino-build.py is Python source and may legitimately contain
        # documentation URLs or runtime path expressions.  Only generated flags
        # are passed directly to the compiler/linker, so inspect paths there.
        if ABSOLUTE_SPECS_RE.search(flag_metadata):
            errors.append(f"{chip}: compiler/linker flags contain an absolute specs path")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--libs-dir", required=True, type=Path)
    parser.add_argument("--targets", default=",".join(DEFAULT_TARGETS))
    args = parser.parse_args()

    targets = [item.strip() for item in args.targets.split(",") if item.strip()]
    errors = validate(args.libs_dir, targets)
    if errors:
        print("PioArduino artifact validation failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print(f"PioArduino artifact validation passed for: {', '.join(targets)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
