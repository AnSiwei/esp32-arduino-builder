# ESP32 Arduino 全芯片自动编译平台

本仓库是一个**自建 PlatformIO 平台**（基于 pioarduino/espressif32 二次开发），
通过 GitHub Actions 自动编译**全部 ESP32 芯片**（esp32 / s2 / s3 / c2 / c3 / c5 / c6 /
c61 / h2 / p4）的 Arduino 框架与预编译静态库，发布到 GitHub Release。
用户在 `platformio.ini` 里填写平台地址即可直接使用，无需手动下载任何库。

```ini
[env:esp32s3]
platform = https://github.com/AnSiwei/esp32-arduino-builder.git
board = esp32-s3-devkitc-1
framework = arduino
```

## 整体架构

```
┌────────────────────────────────────────────────────────────────┐
│ GitHub (github.com)            AnSiwei/esp32-arduino-builder   │
│                                                                │
│  .github/workflows/build-libs.yml  定时构建（每日 02:00）        │
│  platform.json                     平台清单（framework 指向固定 │
│                                    URL 的 Release asset）       │
│  platform.py + builder/            平台安装/构建逻辑             │
│  boards/*.json                     板子定义（仅 Espressif /     │
│                                    M5Stack / Seeed Studio）     │
│                                                                │
│  Releases:                                                     │
│   ├─ libs-<arduino>-<idf>     esp32-arduino-libs.zip           │
│   │                           framework-arduinoespressif32.tar.xz
│   ├─ framework-latest         固定名 framework 包 + 指纹文件     │
│   └─ debug-run-<id>           失败构建的诊断快照（prerelease）    │
└────────────────────────────────────────────────────────────────┘
          │ GitHub Actions runner（Docker: esp32-arduino-lib-builder 镜像）
          ▼
   lib-builder 编译 10 个芯片 → 打包 → 校验 → 发布
```

## 构建流水线（build-libs.yml）

1. **解析版本**：`git ls-remote` 取 `espressif/arduino-esp32` 最新 tag；
   从该 release 页面标题提取配套 ESP-IDF 版本（如 `3.3.11 → v5.5.5`）。
   支持手动触发时指定 `targets` / `arduino_version` / `idf_version`。
2. **增量判断**：与 `.last_build_versions`（提交在仓库里）比对 arduino/idf
   版本；上次构建未完成（`complete != yes`）时强制重跑。手动触发总是构建。
3. **编译**：lib-builder 容器内 `build.sh` 一次编译全部目标芯片。
   构建前自动打多个补丁（见下文「CI 补丁清单」）。
4. **诊断快照**：无论编译成败，把 lib-builder 原始输出归档上传到
   `debug-run-<run_id>` prerelease（分卷 ≤800MB），失败也可排查。
5. **打包与校验**：
   - `normalize_pio_specs.py` 规范化 picolibc specs 路径；
   - `validate_pio_libs.py` / `validate_pio_package.py` 拒绝 nano.specs、
     构建容器绝对路径等错误产物；
   - `package_framework.py` 组装完整 framework 包（arduino 源码 +
     补丁后的 `pioarduino-build.py` + 全芯片 libs）并生成指纹文件。
6. **正式发布**（仅当构建了完整芯片集时）：
   - `libs-<arduino>-<idf>` release：`esp32-arduino-libs.zip` +
     `framework-arduinoespressif32.tar.xz`（**固定名覆盖上传**）；
   - `framework-latest` release：固定名 framework 包 +
     `framework-fingerprint.json` 指纹（platform.py 增量检测用）；
   - 清理 release 内所有历史 asset，只保留当前构建；
   - `platform.json` 指向固定 URL，内容无变化时不产生 git 提交；
   - 写入 `.last_build_versions`（含 `complete=yes`）提交回 develop。

### 发布新鲜度机制（固定 URL + 指纹比对）

PlatformIO 对 URL 包有「同 URI 跳过重装」和「30 天下载缓存」两个行为，
因此固定 URL 不会自动拿到新构建。解决方式：

- 每次构建生成 `framework-fingerprint.json`（含 arduino/idf 版本 + 日期 +
  sha256 短指纹），上传到 `framework-latest`；
- `platform.py` 的 `_ensure_framework_fresh()` 在每次构建前下载该小文件，
  与本地缓存指纹比对；**不一致则清除 PM 下载缓存并卸载已装 framework 包**，
  强制重新下载新产物。用户无需任何手动操作。

## CI 补丁清单（build-libs.yml 内置）

| 补丁 | 原因 |
|------|------|
| `unset GITHUB_TOKEN` + `GITHUB_REPOSITORY_OWNER=espressif` | GitHub Actions 注入的 GITHUB_TOKEN 只能访问当前仓库，无法访问 espressif/arduino-esp32；owner 需强制设为 espressif |
| sed 修复 `install-arduino.sh` tag checkout | `-A 3.3.11` 指定 tag 时 `origin/<tag>` 不存在 |
| 从 `arduino_tinyusb/CMakeLists.txt` 删除 `vendor_host.c` | TinyUSB master 已删除该文件但 CMake 仍引用 |
| `defconfig.common` 追加 `CONFIG_LIBC_PICOLIBC=y` | IDF v5.5 默认 newlib，而 arduino-esp32 3.3.x 预编译库需要 picolibc multilib |
| 本地 overlay espressif/cbor 组件 | 0.6.1~4 的 `open_memstream` 是 glibc 专用，picolibc 下编译失败 |

## 首次部署

1. **推送仓库到 GitHub**（develop 分支为主分支）。
2. **权限**：GitHub Actions 使用自动注入的 `GITHUB_TOKEN`（workflow 已声明
   `permissions: contents: write`），无需额外配置 Secret。
3. **Runner**：使用 GitHub 托管 runner，能直接拉取
   `espressif/esp32-arduino-lib-builder:release-v5.5`（Docker Hub 官方镜像）。
4. **手动触发一次**构建验证（Actions → Build ESP32 Arduino Libs → Run）。

## 用户侧使用

### 基本用法

```ini
[env:esp32c3]
platform = https://github.com/AnSiwei/esp32-arduino-builder.git
board = esp32-c3-devkitm-1
framework = arduino
```

- Xtensa 芯片（esp32/s2/s3）→ `toolchain-xtensa-esp-elf`
- RISC-V 芯片（c2/c3/c5/c6/c61/h2/p4）→ `toolchain-riscv32-esp`

### 可选配置

| platformio.ini 选项 | 说明 |
|------|------|
| `custom_sdkconfig` | 自定义 sdkconfig 片段；变化时平台自动重编译库 |
| `custom_relinker_function` / `custom_relinker_library` / `custom_relinker_object` | Arduino Relinker（三选项必须同时提供） |
| `custom_component_remove` / `custom_component_add` | 增删 IDF 组件 |
| `monitor_filters = esp32_exception_decoder` | 自动安装 tool-esp-rom-elfs |

### ESP-IDF 框架

`framework = espidf` 走 `framework-espidf`（tasmota 预编译 v5.5.4），
与 Arduino 框架可共存（Arduino as component）。

## 编译产物结构

`framework-arduinoespressif32.tar.xz`（≈350MB）解压后：

```
├── package.json                 ← PlatformIO 清单（含 fingerprint）
├── tools/
│   ├── pioarduino-build.py      ← 构建脚本（libs 路径已补丁）
│   └── esp32-arduino-libs/
│       ├── package.json / tools.json
│       ├── esp32/  esp32s2/  esp32s3/  esp32c2/ ... esp32p4/
│       │   ├── lib/        ← .a 静态库（picolibc multilib）
│       │   ├── include/    ← 头文件
│       │   ├── ld/         ← 链接脚本（sections.ld）
│       │   ├── flags/      ← c/cpp/ld/S flags + defines/includes
│       │   ├── bin/        ← bootloader 等
│       │   └── pioarduino-build.py
│       └── sdkconfig            ← 存在时表示 custom sdkconfig 构建
```

> **注意**：预编译库不提交进 git——其中包含超长路径文件（Matter/
> connectedhomeip 头文件 >260 字符），会导致 Windows 上 git clone 失败
> （`Filename too long`，需 `git config core.longpaths true`）。

## 已验证版本组合

| 组件 | 版本 |
|------|------|
| arduino-esp32 | 3.3.11 |
| ESP-IDF | v5.5.5（分支 release/v5.5） |
| lib-builder 镜像 | release-v5.5 |
| 工具链 | xtensa/riscv32-esp-elf 15.2.0（pioarduino registry） |

2026-09-03 已对 run112 产物做端到端验证：esp32s3 / esp32c3 双环境
`pio run` 编译通过，picolibc specs 正确注入链接。

## 已知注意事项

- **中文编码**：GitHub release 的 name/body 与 git commit message 一律用
  纯 ASCII；Windows PowerShell 调 `curl.exe` 传中文参数会因 GBK 转换损坏
  （必须用中文时把 JSON 写成 UTF-8 文件再 `-d @file.json`）。
- **lib-builder 容器无 zip 命令**，打包统一用 `python3 zipfile`。
- **部分芯片手动构建**（targets 子集）只产出诊断快照，
  不会覆盖正式 release，防止残缺产物污染平台。
- 上游 arduino-esp32 更新后最多延迟 24 小时自动编译。
