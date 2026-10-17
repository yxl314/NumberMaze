# standardize

C++ 代码排版与 clang-tidy 风格整理。跑在现代机器上——**Python >= 3.11**，
可以作为正常 Python 包安装。

门禁（构建、测试、内存检查）属于另一个项目 `ci/`，本项目不含任何测试概念，
也不与它共享 Python 代码。两者的关系与协作方式见仓库根的
[CONTRACT.md](../CONTRACT.md)。

## 安装

```bash
pipx install .            # 或 uv tool install .
cxx-standardize --version
```

源码直跑也可以（`PYTHONPATH=src python3 -m cxx_standardize.cli`），但配置要显式
给出——装成包之后工具目录里没有 `config/`。

依赖清单在仓库根的 [requirements/](../requirements/) 下（与 `pyproject.toml`
同源生成，勿手改）：运行、可选 pip clang 与开发依赖都在 `standardize.txt`。
系统 clang（17 及以上）这样装：

```bash
sudo apt-get install clang-format-17 clang-tidy-17 clang-tools-17
```

## 接入一个新工程：两条命令

```sh
python3 cxx_ci/standardize/standardize.py --config config/standardize.py doctor
python3 cxx_ci/standardize/standardize.py --config config/standardize.py --apply
```

* **配置**：照 `config-example/standardize_config.py` 抄一份到工程根，按注释改。每项的含义、
  推导规则与改动时机都写在那里。
* **`doctor`**：两段——环境（含平台级别与判据、`CLANG_PREFER` 生效值、依赖清单
  校验）与生效配置（每项标来源）。
* 一次修完排版 + 风格 + 命名就用 `--apply`（缺省动作是 `all`，已包含命名；
  命名改名也在此一步，位置取自 clang-tidy、目标名按 `NAMING_PREFIX` 口径表算）。

## 支持面

GNU/Linux 全部能力；macOS / BSD 可以跑排版与风格（内存检查是 ci 侧的事）；
其余平台启动即以退出码 2 拒绝。详见 `CONTRACT.md`。

## 使用

```bash
cxx-standardize check                  # 风格 + 排版门禁（预览，不改文件）
cxx-standardize guard                  # 检查宏守卫与 detail:: 归属
cxx-standardize guard --fix            # 补齐或改正头文件宏守卫
cxx-standardize style                  # 只做风格
cxx-standardize format                 # 只做排版
cxx-standardize style --apply          # 写入风格修复
cxx-standardize format --apply         # 写入排版

cxx-standardize format --accept-baseline --yes   # 一次性切换，建立排版基线
cxx-standardize check --jobs 16        # 并行度
cxx-standardize check --verbose        # 透传工具自身的进度等过程噪声
cxx-standardize check --config <路径>  # 指定项目配置
```

退出码（对外契约，编排脚本按它决定后续动作）：

| 码 | 含义 |
| --- | --- |
| 0 | 无需改动 |
| 1 | 有待改动或检查未通过 |
| 2 | 工具或配置错误（缺 clang、配置缺失、基线需重新确认） |

`--jobs`、`--verbose`、`--accept-baseline` 都是一级参数：交互入口该有的能力不
藏在配置里。

## 配置

`config/config.py`（由 `config-example/standardize_config.py` 复制而来）是一个 Python 模块，路径
一律写成 `ROOT / "..."`。装成包时用 `--config` 指定，或用环境变量
`CXX_STANDARDIZE_CONFIG`。

| 项 | 说明 |
| --- | --- |
| `ROOT` | 被处理的项目根 |
| `BUILD_TOOL` / `BUILD_FILE` / `BUILD_DIR` | 构建工具与主构建目录 |
| `TEST_BUILD_FILE` / `TEST_BUILD_DIR` | 测试构建文件与目录 |
| `CXX_COMPILER` / `CXX_STANDARD` | 编译器与语言标准 |
| `CLANG_FORMAT_CONFIG` / `CLANG_TIDY_CONFIG` | 两个配置文件的位置，**必须真实存在**。现成的全量示例见 `config-example/clang-config/` |
| `CLANG_MAJOR` | 钉定 clang 主版本；多版本共存时结果随版本变化 |
| `CLANG_TIDY_EXTRA_ARGS` | 额外编译参数，用于屏蔽第三方头文件的已知误报 |
| `FORMAT_COMMENTS` | 注释保护模式：`protect`（推荐）/ `none`（旧名 `protect-column`/`placeholder` 等价于 `protect`） |
| `TIDY_PREPARE` | 分析前先生成 moc_/ui_/qrc_ 等派生文件 |
| `VERIFY_COMMAND` | 应用风格修复后验证构建的指令；留空则跳过验证（会明确提示） |
| `FROZEN` | 禁改区：`--apply` 绝不可写的路径（vendored 冻结库）。与 `BLACKLIST` 分列——黑名单是"不必分析"，这里是"绝不可写" |
| `BOUNDARY_DIRS` / `GUARD_EXTS` | 受管源目录与需要检查宏守卫的头文件后缀 |
| `DETAIL_DIRS` / `DETAIL_MARK` | 允许出现 `detail::` 的目录与标记写法 |
| `TIDY_HEADER_FILTER` | 强制注入的 header-filter；命令行优先于项目 `.clang-tidy` |
| `XFAIL_FILE` / `XFAIL_STRICT` | 风格侧登记清单的位置与严格模式 |
| `CI_LOG_DIR` / `JOBS` | 日志目录（缺省 `BUILD_DIR/ci-logs`）与并行度 |

**两个 clang 配置文件必须真实存在**：配置里点了名却找不到时会直接失败，不会
退回工具自身的"向上查找"——那会让实际生效的配置随运行目录变化，也进不了基线
指纹。

## 编译数据库

clang-tidy 靠 `compile_commands.json` 解析编译参数，缺了会把包含路径全判成
编译错误。**取库方式由构建系统决定，没有候选链**：

| 构建系统 | 来源 | 缺了怎么办 |
| --- | --- | --- |
| cmake | cmake 自己导出的那份（`-DCMAKE_EXPORT_COMPILE_COMMANDS=ON`） | 报"先跑一次构建"，不自己解析 |
| qmake / qmake6 | `compiledb`（`pip install compiledb`） | 报该装什么，不退回自建解析 |

链上留一个备选，那条备选路径的代码就永远不会被删掉——"用现成工具"这件事也就
从未真正发生，所以这里不设 `bear`、`ninja -t compdb` 之类的备选。

`compiledb` 解析 `make -n` 的输出而不真跑构建。它按 `make[N]: Entering directory`
判断子目录，中文 locale 下 make 打的是"进入目录"，目录会全停在构建根、条目全废——
因此调用时固定 `LC_ALL=C`，本工具已经这么做。dry-run 中途失败会产出一份"能读但
残缺"的数据库，这种半份结果比失败更难发现，因此一旦发现迹象就判为不可用。

主工程与测试是两套构建，**两份库都会取，再合并成一份**放到日志目录：只给主库的
话，测试源（要 QtTest 之类的包含路径）会整片判成"无法分析"。

## 注释保护

排版**不触碰注释**：先摘掉注释排代码，再用 difflib 按代码行对齐把注释回填。
行尾注释钉回作者原来的列号；只有代码对齐后越过该列时才右移一格让开。独占一行
的注释按原缩进挂回它下方那条代码之前。

clang-format 没有保列号的等价能力（`AlignTrailingComments` 会重排，改成 `false`
会塌成一个空格，`{Kind: Leave}` 保的是间隔而不是列号），因此这一层由本工具做。

`FORMAT_COMMENTS`：`protect`（默认，保护注释）/ `none`（完全信任 `.clang-format`；
旧名 `protect-column` / `placeholder` 等价于 `protect`）。

## 排版基线

排版结果必须可复现，因此有一个指纹：**clang-format 版本 + 注释保护模式 + 配置
文件内容摘要**。三者任一变化都会让旧基线失效，提示重新确认，而不是把差异当成
代码问题。

首次接入时存量仓库必然有漂移：

```bash
cxx-standardize format                            # 1. 预览漂移
cxx-standardize format --accept-baseline --yes    # 2. 一次性切换并记下基线
cxx-standardize format                            # 3. 之后纳入门禁
```

非交互环境执行 `--accept-baseline` 必须显式给 `--yes`，绝不默默改写。排版结果
连跑两次不一致（不幂等）时拒绝应用——那说明基线本身不成立。

## 风格与登记清单

诊断来自 clang-tidy 的 `--export-fixes`（官方机器接口），**检查名是权威字段**，
不从人读文本的方括号里猜；修复由 `clang-apply-replacements` 应用，跨文件的改名
替换交给官方应用器。

诊断**包含头文件**，包不包含、包含哪些由项目 `.clang-tidy` 的
`HeaderFilterRegex` 决定。摘要因此把口径摆出来：

```
风格诊断 1461 条（源文件 288 / 头文件 1173），涉及 29 个文件（源 13 / 头 16）
```

条数按诊断算，括号里的文件数是涉及的文件个数，两者不同：一个头文件里的一条
检查可以命中几十处。

存量违规用 `standardize.xfail` 登记（纯文本，一行一条，`#` 起注释）：

```
[style]
Source/CDBurn/Util.cpp:readability-identifier-naming
Source/CDBurn/Util.cpp:120:bugprone-branch-clone   # 待重构
```

行号可省略（表示该文件全部行）。命中的不计失败但仍回显并标注 `(xfail)`；未
登记的新违规照判失败；登记项已修复则提示可移除，`XFAIL_STRICT = True` 时直接
判失败。只查了改动集时，未检查文件的登记项不会被误报成"已修复"。

门禁侧另有一份 `ci.xfail`（登记用例与内存符号），归另一个项目管；本项目不读
也不解析它。

## 禁改区写保护

`SCOPE` / `WHITELIST` / `BLACKLIST` 管的是"**分析**谁"；它们不拦"**写到哪**"。
`--apply` 走 clang-apply-replacements 时，替换块来自翻译单元的诊断，落点可能在
处理范围之外——一个把冻结库头文件带进诊断的 TU，就能让自动修复把改名或排版直接
写进那份不该动的库。

**写前查落点**：替换块报出的每个落点（`DiagnosticMessage.Replacements[].FilePath`，
不是命令行目标）落在 `FROZEN` 或 `BLACKLIST` 里 → 整体拒绝，指名文件与命中哪张表。
黑名单与禁改区分列是为了语义清楚（一个宽容、一个红线），但"fix-it 落到没让你管的
地方"是同一类越界写入，因此保护范围一致。

## header-filter 强制注入

`TIDY_HEADER_FILTER` 非空时会作为 `--header-filter=<正则>` 直接传给 clang-tidy：
**命令行优先于项目 `.clang-tidy` 的 `HeaderFilterRegex`**。实测把项目的值改成 `.*`
会让同一份代码的诊断从 0 条涨到 1588 条（Qt 头全涌进来），而 `--apply` 也可能写到
那里；反过来收窄会把本仓头文件的诊断静默藏掉。两条通道各管各的：`Checks:` 仍走
`--config-file`，header-filter 走命令行。

## 应用修复与构建验证

改名类修复可能只改声明处而让别的文件编译不过，因此 `--apply` 先整体应用全部
修复，再跑一次 `VERIFY_COMMAND`：通过则保留，失败则**整体回到改前状态**。

`VERIFY_COMMAND` 留空时跳过验证（会明确提示）。想复用门禁项目的构建，可以写：

```python
VERIFY_COMMAND = ["python3", str(ROOT / ".." / "ci" / "ci.py"), "--only", "build"]
```

这是命令行契约层面的协作，不是 import——两个项目仍然不共享代码。

## 目录结构

```
standardize/
├── pyproject.toml              requires-python = ">=3.11"
├── src/cxx_standardize/
│   ├── cli.py                  入口（console_scripts: cxx-standardize）
│   ├── config.py               配置加载与校验
│   ├── errors.py               错误码与退出码投影
│   ├── models.py               共享数据类型（Finding / Entry / Scope …）
│   ├── log.py                  输出与过程噪声
│   ├── fmt.py                  clang-format 封装与工具解析
│   ├── tidy.py                 clang-tidy 结构化诊断与修复
│   ├── style.py                风格检查与整理
│   ├── format.py               排版、幂等自检与一次性切换
│   ├── comments.py             注释保护
│   ├── baseline.py             排版基线指纹
│   ├── compile_db.py           编译数据库：按构建系统取一份
│   ├── incremental.py          git 改动集
│   └── xfail.py                风格侧登记清单
├── tests/
└── README.md

config-example/clang-config/          写全了排版与命名规则的现成配置
├── .clang-format               排版规范（可 clang-format --style=file 直接跑）
├── .clang-tidy                 检查开关 + 命名规则
├── naming-tables.py            配套的命名口径表（粘进 config/standardize.py）
└── README.md                   怎么落地、哪里要按工程改
```

## 测试与自检

```bash
cd standardize && python3 -m pytest tests -q
```

需要 clang-format 17 以上的用例会自动跳过；其余用例用假工具驱动，验证的是流程
与判定（登记清单命中、口径行、基线重新确认、修复破坏构建时回退）。

自身代码另有两项静态检查，随开发依赖一起装：

```bash
pip install -e ".[dev]"
ruff check src tests        # 版式与常见错误
mypy --check-untyped-defs   # 类型（配置见 pyproject.toml）
```

`ruff` 的忽略项写在 `pyproject.toml` 里，各有理由：`RUF059`（`_, code, message`
是本项目的错误码风格，占位的那一位就是不用）、`EXE001`（测试里的假工具由
`fake_tool()` 在运行时 chmod）；`__init__.py` 单独放行 `UP031`/`UP036`，因为版本
闸与它的提示必须能被 Python 3.4 解析，老机器上直跑源码时先于一切报错。

## 依赖与已验环境

- Python **3.11+**（`pyproject.toml` 的 `requires-python`）；运行依赖见
  [requirements/standardize.txt](../requirements/standardize.txt)，与
  `pyproject.toml` 同源生成。
- 已实跑验证的解释器：**3.14**（本机 `python3 --version` = 3.14.4）。
  `run_tests.sh` 会按 `python3.11` / `python3.12` / `python3.13` / `python3.14`
  的顺序把矩阵跑一遍，装了哪个跑哪个，没装的跳过并说明。
- clang 工具链：**17**（`clang-format` / `clang-tidy`）。主版本由 `CLANG_MAJOR`
  钉定；需要精确复现时用 `CLANG_FORMAT_BIN` / `CLANG_TIDY_BIN` 显式指定路径，
  工具解析到的真实路径会进基线指纹。
