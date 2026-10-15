# 安装

本仓库有两个**独立项目**，安装方式不同（关系见 [CONTRACT.md](CONTRACT.md)）。
按使用场景照抄下面任一段即可。依赖清单都在 [requirements/](requirements/) 下，
正文不再手抄包名。

## 场景一：老机器无网（只跑门禁 `ci`）

`ci/` 只用标准库，**零第三方依赖**——不需要 pip，也不需要联网。

1. 把 `ci/` 整个目录拷到目标机器。它是自包含的：入口 `ci.py`、`lib/`、
   `stages/`、`config/config.py.example` 都在里面。
2. 复制一份配置，放在**项目根**（别放进工具目录）：
   ```sh
   mkdir -p config
   cp ci/config/config.py.example config/ci.py
   ```
   按注释改 `ROOT`、`BUILD_TOOL` 等；`CONTRACT_VERSION` 保持与本工具一致，或
   干脆不写（取工具缺省）。
3. 运行：
   ```sh
   python3 ci/ci.py --config config/ci.py
   ```

依赖清单见 [requirements/ci.txt](requirements/ci.txt)（空清单，用于让"零依赖"
成为可审计的事实）。外部工具（`cmake` / `qmake`、`valgrind` 或 ASan 运行库）按需
安装；缺了阶段标 `[SKIPPED]` 并给原因，不判红。

## 场景二：现代机器（排版与风格 `standardize`）

需要 **Python >= 3.11**。

```sh
pipx install ./standardize                       # 或 uv tool install ./standardize
pip install -r requirements/standardize.txt      # 运行依赖（PyYAML）
cxx-standardize --version
```

再按需装系统 clang 17：

```sh
sudo apt-get install clang-format-17 clang-tidy-17 clang-tools-17
```

配置放在项目根，用 `--config config/standardize.py` 指定（装成包后工具目录里
没有 `config/`，显式指定是常态）：把 `standardize/config/config.py.example` 抄
过去按注释改。

## 场景三：机器上没有系统 clang 17（改用 pip 的 clang）

解析顺序是"先看 pip 包自带的二进制，再退回 PATH"（见
[standardize-clang17.txt](requirements/standardize-clang17.txt) 与
`fmt.wheel_binary`）。没有系统 clang 时，用 pip 装一份：

```sh
pip install -r requirements/standardize-clang17.txt
```

要改用系统那一份，在配置里写 `CLANG_FORMAT_BIN` / `CLANG_TIDY_BIN` 显式指定
（写了就必须存在，不退回 PATH 搜索）。

## 两个项目都装好后：一把梭

```sh
./run_all.sh                    # 顺序调用 ci.py 与 cxx-standardize
```

编排脚本只调两侧的 CLI，不 import 任何一侧内部模块。契约版本校验已下移到各侧
入口：配置与工具取自不同代时，两侧任一个都会在加载配置时报错并退 2。

## 开发

```sh
pip install -r requirements/dev.txt              # pytest / ruff / mypy
(cd standardize && python3 -m pytest tests -q)   # 标准侧测试
python3 ci/tests/run.py                          # 门禁侧测试
python3 tools/dupcheck.py --check                # 重复度基线
python3 tools/export_requirements.py --check     # 依赖清单与 pyproject 不漂
```
