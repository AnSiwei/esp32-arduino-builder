#!/usr/bin/env bash
# 在 lib-builder 容器内准备构建环境（补丁脚本）。
# 拆分后每个芯片 job 都会调用本脚本，因此把公共补丁逻辑集中到这里。
# 调用前需在 workflow 中设置好环境变量：
#   unset GITHUB_TOKEN
#   export GITHUB_REPOSITORY_OWNER=espressif
#   export GITHUB_REPOSITORY=espressif/arduino-esp32
set -euo pipefail

cd /opt/esp/lib-builder

# 修复：install-arduino.sh 中 checkout -B "$AR_SOURCE_BRANCH" origin/"$AR_SOURCE_BRANCH"
# 仅对分支有效。当 -A 指定的是 tag（如 3.3.11）时，origin/3.3.11 不存在，
# 导致 "fatal: 'origin/3.3.11' is not a commit and a branch '3.3.11' cannot be created from it"。
# 补丁：fetch 加 --tags，checkout 改为直接检出（兼容 tag 和分支），移除 pull --ff-only（tag 无需 pull）。
sed -i 's|fetch --all|fetch --all --tags|' tools/install-arduino.sh
sed -i 's|checkout -B "$AR_SOURCE_BRANCH" origin/"$AR_SOURCE_BRANCH" && \\|checkout "$AR_SOURCE_BRANCH"|' tools/install-arduino.sh
sed -i '/pull --ff-only/d' tools/install-arduino.sh
echo "install-arduino.sh patched: tag checkout fix applied"

# 修复：TinyUSB commit a57f857 删除了 vendor_host.c（标注为 "remove the obsolete host vendor driver"），
# 但 lib-builder 的 arduino_tinyusb/CMakeLists.txt 仍引用该文件，导致 CMake 报错。
# 正确修复：从 CMakeLists.txt 中删掉对 vendor_host.c 的引用，而非 pin 旧版本。
sed -i '/vendor_host\.c/d' components/arduino_tinyusb/CMakeLists.txt
echo "CMakeLists.txt patched: removed vendor_host.c reference"

# pioarduino-build.py 使用 picolibc multilib；显式把配置追加到
# lib-builder 的公共 defconfig。build.sh 的额外参数只对 -b 非 all
# 构建生效，直接把 CONFIG_*=y 作为 build.sh 参数不会作用于默认的
# 全芯片构建，因此这里必须修改实际传给 idf.py 的 defconfig。
printf '%s\n' \
  '# Required by the PioArduino multilib layout.' \
  'CONFIG_IDF_EXPERIMENTAL_FEATURES=y' \
  'CONFIG_LIBC_PICOLIBC=y' >> configs/defconfig.common
echo "defconfig.common patched: picolibc enabled"

# lib-builder release-v5.5 downloads espressif/cbor 0.6.1~4 during the Arduino-Libs build.
# Its Linux-only open_memstream implementation depends on glibc APIs that picolibc does not provide.
# Overlay the same component locally and disable the optional CBOR-to-JSON stream implementation.
CBOR_VERSION="0.6.1~4"
CBOR_URL="https://components-file.espressif.com/components/espressif/cbor/${CBOR_VERSION}/espressif__cbor-v0.6.1_4.zip"
curl -fsSL --retry 3 "$CBOR_URL" -o /tmp/espressif-cbor.zip
rm -rf components/cbor
mkdir -p components/cbor
python3 -c "import zipfile; zipfile.ZipFile('/tmp/espressif-cbor.zip').extractall('components/cbor')"
python3 - <<'PY'
from pathlib import Path
cmake = Path("components/cbor/CMakeLists.txt")
text = cmake.read_text(encoding="utf-8")
lines = text.splitlines(keepends=True)
output = []
skip = 0
for line in lines:
    if skip:
        skip -= 1
        continue
    if "tinycbor/src/open_memstream.c" in line:
        continue
    if line.strip() == "# for open_memstream.c":
        skip = 3
        continue
    output.append(line)
patched = "".join(output)
if "open_memstream.c" in patched or '__linux__' in patched:
    raise SystemExit("cbor CMakeLists still contains the glibc open_memstream block")
if "target_compile_definitions(${COMPONENT_LIB} PRIVATE WITHOUT_OPEN_MEMSTREAM)" not in patched:
    patched += "\ntarget_compile_definitions(${COMPONENT_LIB} PRIVATE WITHOUT_OPEN_MEMSTREAM)\n"
cmake.write_text(patched, encoding="utf-8")
PY
test -f components/cbor/CMakeLists.txt
grep -q 'WITHOUT_OPEN_MEMSTREAM' components/cbor/CMakeLists.txt
! grep -Eq 'open_memstream\.c|__linux__' components/cbor/CMakeLists.txt
echo "Local cbor overlay patched: WITHOUT_OPEN_MEMSTREAM"

echo "prepare-build.sh: all patches applied"