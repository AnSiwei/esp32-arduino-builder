#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
打包 PlatformIO framework 包（CI 中使用）

在 lib-builder 容器构建完成后运行：
1. 打包 esp32-arduino-libs.zip（预编译静态库，platform.py 注入用）
2. 组装完整 framework 包 framework-arduinoespressif32（arduino-esp32 源码 + pioarduino-build.py + 编译好的 libs）
   并打包为 framework-arduinoespressif32.tar.xz（供 platform.json 的 framework-arduinoespressif32 包使用）

用法：
    python3 scripts/package_framework.py \
        --arduino-src /opt/esp/lib-builder/components/arduino \
        --libs-dir /opt/esp/lib-builder/out/tools/esp32-arduino-libs \
        --out-dir tools \
        --arduino-version 3.3.11 \
        --idf-version v5.5.5 \
        --chip-variant esp32u  # 可选，pioarduino-build.py 补丁用（默认不生成）

注意：YAML run: 块内无法使用 heredoc，因此此脚本以独立文件提交进 git。
"""

import argparse
import io
import json
import lzma
import os
import re
import shutil
import sys
import tarfile
import tempfile
import zipfile
from datetime import date

# PlatformIO 支持的包名
FRAMEWORK_NAME = "framework-arduinoespressif32"


def zip_dir(src, out_path, compresslevel=9):
    """把 src 目录内容打包为 zip（顶层为 src 内的内容，不含 src 本身）"""
    zf = zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=compresslevel)
    for root, dirs, files in os.walk(src):
        for f in files:
            fp = os.path.join(root, f)
            arc = os.path.relpath(fp, src).replace(os.sep, "/")
            zf.write(fp, arc)
        for d in dirs:
            dp = os.path.join(root, d)
            arc = os.path.relpath(dp, src).replace(os.sep, "/") + "/"
            zf.writestr(zipfile.ZipInfo(arc), "")
    zf.close()
    print("zip 打包完成: %s" % out_path)


def make_tar_xz(src, out_path, compresslevel=9):
    """把 src 目录内容打包为 tar.xz（顶层为 src 内的内容）"""
    def _filter(tarinfo):
        # 跳过 .git 等无用目录，减小体积
        if ".git" in tarinfo.name.split("/"):
            return None
        return tarinfo

    with tarfile.open(out_path, "w:xz", preset=compresslevel) as tf:
        for entry in os.listdir(src):
            tf.add(os.path.join(src, entry), arcname=entry, filter=_filter)
    print("tar.xz 打包完成: %s" % out_path)


def gen_framework_package_json(arduino_version, idf_version, out_dir):
    """生成 framework 包的 package.json（PlatformIO 清单）"""
    data = {
        "name": FRAMEWORK_NAME,
        "description": "Arduino Wiring-based Framework for the Espressif ESP32 series of SoCs",
        "keywords": ["framework", "arduino", "espressif", "esp32"],
        "license": "LGPL-2.1-or-later",
        "repository": {
            "type": "git",
            "url": "https://github.com/AnSiwei/esp32-arduino-builder.git",
        },
        "version": "%s+idf%s" % (arduino_version, idf_version.lstrip("v")),
        "date": date.today().isoformat(),
    }
    with io.open(os.path.join(out_dir, "package.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print("framework package.json 已生成: %s" % os.path.join(out_dir, "package.json"))


def gen_fingerprint(arduino_version, idf_version, out_dir):
    """生成 framework 包指纹文件（platform.py 用它判断是否需要强制重装)

    fingerprint 固定名上传到 GitHub Release，内容随每次构建变化。
    platform.py 下载此小文件与已安装 framework 包内记录的指纹比对,
    不一致则卸载旧包并强制重新下载 framework tar.xz。
    """
    import hashlib
    fingerprint = {
        "arduino_version": arduino_version,
        "idf_version": idf_version,
        # build timestamp makes every build unique even for the same versions
        "built_at": date.today().isoformat(),
    }
    raw = json.dumps(fingerprint, sort_keys=True)
    fingerprint["sha256"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    fp_path = os.path.join(out_dir, "framework-fingerprint.json")
    with io.open(fp_path, "w", encoding="utf-8") as f:
        json.dump(fingerprint, f, indent=2, ensure_ascii=False)
        f.write("\n")
    # 同时把指纹写进 framework 包内，供安装后比对
    return fp_path


def patch_pioarduino_build(pioarduino_build_path, is_chip_script=False):
    """补丁 pioarduino-build.py：libs 目录指向 framework 包内的 tools/esp32-arduino-libs

    存在两处需要补丁的引用：
    1. 官方 arduino-esp32 根 `tools/pioarduino-build.py` 用
           FRAMEWORK_LIBS_DIR = platform.get_package_dir("framework-arduinoespressif32-libs")
       独立 libs 包。
    2. lib-builder 编译时为每个芯片生成的 `tools/esp32-arduino-libs/<chip>/pioarduino-build.py`
       用
           FRAMEWORK_SDK_DIR = env.PioPlatform().get_package_dir(
               "framework-arduinoespressif32-libs"
           )
       独立 libs 包。

    统一改为指向 framework 包内的 tools/esp32-arduino-libs（与我们 CI 产物位置一致）。
    注意：脚本可能是 LF 或 CRLF 换行（lib-builder 生成的芯片级脚本是 CRLF），
    用正则 + \\s* 兼容两种换行。
    """
    with io.open(pioarduino_build_path, "r", encoding="utf-8") as f:
        content = f.read()

    new_expr = 'join(FRAMEWORK_DIR, "tools", "esp32-arduino-libs")'

    # 两类引用（各自匹配 LF/CRLF 换行）
    patterns = [
        # 根脚本：platform.get_package_dir("framework-arduinoespressif32-libs")
        re.compile(
            r'platform\.get_package_dir\(\s*"framework-arduinoespressif32-libs"\s*\)'
        ),
        # 芯片级脚本：env.PioPlatform().get_package_dir(\n    "framework-arduinoespressif32-libs"\n)
        re.compile(
            r'env\.PioPlatform\(\)\.get_package_dir\(\s*"framework-arduinoespressif32-libs"\s*\)'
        ),
    ]

    changed = False
    for pat in patterns:
        if pat.search(content):
            content = pat.sub(new_expr, content)
            changed = True

    if not changed:
        # 已打过补丁则跳过
        if new_expr in content:
            return
        tag = "芯片级" if is_chip_script else "根"
        print("警告：%s pioarduino-build.py 中未找到需要替换的字符串！" % tag, file=sys.stderr)
        for line in content.splitlines():
            if "FRAMEWORK" in line and ("SDK_DIR" in line or "LIBS_DIR" in line or "get_package_dir" in line):
                print("  " + line.strip(), file=sys.stderr)
        return

    with io.open(pioarduino_build_path, "w", encoding="utf-8") as f:
        f.write(content)
    print("pioarduino-build.py 已补丁: libs 目录 -> tools/esp32-arduino-libs")


def main():
    parser = argparse.ArgumentParser(description="打包 PlatformIO framework 包")
    parser.add_argument("--arduino-src", required=True,
                        help="arduino-esp32 源码目录（容器内 components/arduino）")
    parser.add_argument("--libs-dir", required=True,
                        help="编译好的 esp32-arduino-libs 目录（容器内 out/tools/esp32-arduino-libs）")
    parser.add_argument("--out-dir", default="tools",
                        help="输出目录（默认 tools/）")
    parser.add_argument("--arduino-version", required=True, help="arduino-esp32 版本号，如 3.3.11")
    parser.add_argument("--idf-version", required=True, help="ESP-IDF 版本号，如 v5.5.5")
    args = parser.parse_args()

    out_dir = os.path.abspath(args.out_dir)
    os.makedirs(out_dir, exist_ok=True)

    # ---------- 1. 打包 esp32-arduino-libs.zip ----------
    libs_zip = os.path.join(out_dir, "esp32-arduino-libs.zip")
    if os.path.exists(libs_zip):
        os.remove(libs_zip)
    zip_dir(args.libs_dir, libs_zip)

    # ---------- 2. 组装并打包 framework 包 ----------
    framework_dir = os.path.join(out_dir, FRAMEWORK_NAME)
    if os.path.exists(framework_dir):
        shutil.rmtree(framework_dir)

    print("复制 arduino-esp32 源码 -> %s" % framework_dir)
    shutil.copytree(args.arduino_src, framework_dir,
                    ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))

    # 复制编译好的 libs 进 framework 包的 tools/
    libs_target = os.path.join(framework_dir, "tools", "esp32-arduino-libs")
    if os.path.exists(libs_target):
        shutil.rmtree(libs_target)
    print("复制编译好的 libs -> %s" % libs_target)
    shutil.copytree(args.libs_dir, libs_target)

    # 补丁 pioarduino-build.py（根脚本 + 每个芯片目录下的芯片级脚本）
    pioarduino_build = os.path.join(framework_dir, "tools", "pioarduino-build.py")
    if os.path.exists(pioarduino_build):
        patch_pioarduino_build(pioarduino_build, is_chip_script=False)
    else:
        print("警告：pioarduino-build.py 不存在！%s" % pioarduino_build, file=sys.stderr)
    # 芯片级脚本：tools/esp32-arduino-libs/<chip>/pioarduino-build.py
    libs_in_fw = os.path.join(framework_dir, "tools", "esp32-arduino-libs")
    if os.path.isdir(libs_in_fw):
        for chip in sorted(os.listdir(libs_in_fw)):
            chip_py = os.path.join(libs_in_fw, chip, "pioarduino-build.py")
            if os.path.isfile(chip_py):
                patch_pioarduino_build(chip_py, is_chip_script=True)

    # 生成 package.json
    gen_framework_package_json(args.arduino_version, args.idf_version, framework_dir)

    # 生成指纹文件（release asset），并把指纹写入 framework 包内 package.json
    fp_path = gen_fingerprint(args.arduino_version, args.idf_version, out_dir)
    with io.open(fp_path, encoding="utf-8") as f:
        fingerprint = json.load(f)
    pkg_json_path = os.path.join(framework_dir, "package.json")
    with io.open(pkg_json_path, encoding="utf-8") as f:
        pkg_data = json.load(f)
    pkg_data["fingerprint"] = fingerprint["sha256"]
    with io.open(pkg_json_path, "w", encoding="utf-8") as f:
        json.dump(pkg_data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print("framework fingerprint: %s -> %s" % (fingerprint["sha256"], fp_path))

    # 打包 tar.xz
    framework_tarxz = os.path.join(out_dir, "%s.tar.xz" % FRAMEWORK_NAME)
    if os.path.exists(framework_tarxz):
        os.remove(framework_tarxz)
    make_tar_xz(framework_dir, framework_tarxz)

    # ---------- 3. 汇总 ----------
    ok = True
    for name in ("esp32-arduino-libs.zip", "%s.tar.xz" % FRAMEWORK_NAME):
        p = os.path.join(out_dir, name)
        if os.path.exists(p):
            print("[OK] %s: %d bytes (%.1f MB)" % (name, os.path.getsize(p), os.path.getsize(p) / 1048576))
        else:
            print("[FAIL] %s packaging failed" % name, file=sys.stderr)
            ok = False
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
