#!/usr/bin/env python3
"""Validate the directory and archives produced by the ESP32 library workflow."""

from __future__ import annotations

import argparse
import json
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

try:
    from validate_pio_libs import DEFAULT_TARGETS, validate
except ModuleNotFoundError:  # Support importing this file from the repository root.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from validate_pio_libs import DEFAULT_TARGETS, validate

LEGACY_CHIPS = {"esp32", "esp32s2"}
REQUIRED_CHIP_DIRS = ("lib", "include", "bin")


def targets_from_arg(value: str) -> list[str]:
    targets = [item.strip() for item in value.split(",") if item.strip()]
    if not targets:
        raise ValueError("target list is empty")
    if len(set(targets)) != len(targets):
        raise ValueError("target list contains duplicates")
    unknown = sorted(set(targets) - set(DEFAULT_TARGETS))
    if unknown:
        raise ValueError("unsupported targets: " + ", ".join(unknown))
    return targets


def load_json(path: Path) -> object:
    raw = path.read_bytes()
    # Some upstream lib-builder versions emit UTF-16 JSON.  Accept it while
    # still requiring valid JSON; the archive itself is checked separately.
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            return json.loads(raw.decode(encoding))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    raise ValueError(f"invalid JSON: {path}")


def validate_directory(libs_dir: Path, targets: list[str]) -> list[str]:
    errors = validate(libs_dir, targets)
    for name in ("package.json", "tools.json"):
        path = libs_dir / name
        if not path.is_file():
            errors.append(f"libs package missing {name}")
        else:
            try:
                value = load_json(path)
                if not isinstance(value, dict):
                    errors.append(f"libs package {name} is not a JSON object")
            except ValueError as exc:
                errors.append(str(exc))

    for chip in targets:
        chip_dir = libs_dir / chip
        for required in REQUIRED_CHIP_DIRS:
            path = chip_dir / required
            if not path.is_dir() or not any(path.iterdir()):
                errors.append(f"{chip}: missing or empty {required}/")
        if chip not in LEGACY_CHIPS:
            if not (chip_dir / "pioarduino-build.py").is_file():
                errors.append(f"{chip}: missing pioarduino-build.py")
            flags = chip_dir / "flags"
            if not flags.is_dir():
                errors.append(f"{chip}: missing flags/")
            if not (chip_dir / "ld").is_dir():
                errors.append(f"{chip}: missing ld/")
    return errors


def safe_member(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts


def validate_libs_zip(path: Path, targets: list[str]) -> list[str]:
    errors: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            bad = archive.testzip()
            if bad:
                errors.append(f"libs zip has corrupt entry: {bad}")
            names = set(archive.namelist())
            for name in names:
                if not safe_member(name):
                    errors.append(f"libs zip has unsafe path: {name}")
            for required in ("package.json", "tools.json"):
                if required not in names:
                    errors.append(f"libs zip missing {required}")
            for chip in targets:
                prefix = chip + "/"
                if not any(name.startswith(prefix + "lib/") for name in names):
                    errors.append(f"libs zip missing {chip}/lib/")
                if not any(name.startswith(prefix + "include/") for name in names):
                    errors.append(f"libs zip missing {chip}/include/")
                if not any(name.startswith(prefix + "bin/") for name in names):
                    errors.append(f"libs zip missing {chip}/bin/")
    except (OSError, zipfile.BadZipFile) as exc:
        errors.append(f"cannot read libs zip {path}: {exc}")
    return errors


def validate_framework_tar(path: Path, targets: list[str]) -> list[str]:
    errors: list[str] = []
    try:
        with tarfile.open(path, mode="r:*", errorlevel=2) as archive:
            names = set(archive.getnames())
            for name in names:
                if not safe_member(name):
                    errors.append(f"framework tar has unsafe path: {name}")
                if ".git" in PurePosixPath(name).parts:
                    errors.append(f"framework tar contains .git path: {name}")
            for required in (
                "package.json",
                "tools/pioarduino-build.py",
                "tools/esp32-arduino-libs/package.json",
            ):
                if required not in names:
                    errors.append(f"framework tar missing {required}")
            for chip in targets:
                prefix = f"tools/esp32-arduino-libs/{chip}/"
                if not any(name.startswith(prefix + "lib/") for name in names):
                    errors.append(f"framework tar missing {prefix}lib/")
                script_name = prefix + "pioarduino-build.py"
                if chip not in LEGACY_CHIPS and script_name not in names:
                    errors.append(f"framework tar missing {script_name}")
            # Confirm the package-framework path rewrite really happened.  A
            # stale independent package reference makes the archive unusable.
            for name in ("tools/pioarduino-build.py",):
                if name in names:
                    member = archive.extractfile(name)
                    if member and "framework-arduinoespressif32-libs" in member.read().decode("utf-8", "replace"):
                        errors.append(f"framework tar has stale package reference: {name}")
            for chip in targets:
                if chip in LEGACY_CHIPS:
                    continue
                name = f"tools/esp32-arduino-libs/{chip}/pioarduino-build.py"
                if name in names:
                    member = archive.extractfile(name)
                    if member and "framework-arduinoespressif32-libs" in member.read().decode("utf-8", "replace"):
                        errors.append(f"framework tar has stale package reference: {name}")
    except (OSError, tarfile.TarError) as exc:
        errors.append(f"cannot read framework tar {path}: {exc}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--libs-dir", required=True, type=Path)
    parser.add_argument("--libs-zip", required=True, type=Path)
    parser.add_argument("--framework-tar", required=True, type=Path)
    parser.add_argument("--targets", default=",".join(DEFAULT_TARGETS))
    args = parser.parse_args()
    try:
        targets = targets_from_arg(args.targets)
    except ValueError as exc:
        print(f"artifact validation failed: {exc}", file=sys.stderr)
        return 2

    errors = []
    errors.extend(validate_directory(args.libs_dir, targets))
    errors.extend(validate_libs_zip(args.libs_zip, targets))
    errors.extend(validate_framework_tar(args.framework_tar, targets))
    if errors:
        print("Generated artifact validation failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(f"Generated artifact validation passed for: {', '.join(targets)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
