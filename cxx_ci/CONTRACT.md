# 跨项目契约

本仓库有两个**独立项目**，它们不共享任何 Python 模块，只通过文件与命令行
协作。本文是它们之间唯一的约定；两侧各自的 README 讲自己的用法。

```
cxx_ci/
├── ci/             Python >= 3.4，零第三方依赖，跑在老机器上
├── standardize/    Python >= 3.11，pip 可装，跑在现代机器上
└── CONTRACT.md     本文件
```

## 一、契约版本

```
CONTRACT_VERSION = 3
```

两侧各自在自己的配置里声明这个版本（`ci/config/config.py` 与
`standardize/config/config.py`），谁都不读对方的文件——两侧之间没有共享代码，
猜对方的格式只会更糟。

`CONTRACT_VERSION` 是**工具的属性**，不是项目的必填项：`ci/lib/version.py` 与
`standardize/src/cxx_standardize/version.py` 各声明一份，必须相等。配置写不写都
行，但一旦写了就必须与所用工具一致。**各侧入口在加载配置时自己比对**——不一致
说明配置与工具取自不同代，直接拒绝执行（退出码 2，见下面的 `--version`）。编排
脚本 `run_all.sh` 因此退化为纯编排，不再读任一侧的内部模块。

两侧的 `--version` 输出**同一形状**：

```
<项目> <x.y.z> contract <N> python >=<下限>
```

（`ci 1.3.0 contract 3 python >=3.4` / `cxx-standardize 1.0.0 contract 3
python >=3.11`）。机器可读报告的 `tool` 段也带工具版本与契约号，产物因此能自证
"是哪一版跑出来的"。`--version` 不需要配置，必须在加载配置之前就能返回。

## 二、职责边界

| 项目 | 职责 | 明确不做 |
| --- | --- | --- |
| `ci` | 构建、测试、内存检查、门禁编排 | 不感知 clang，不改写工作区 |
| `standardize` | 代码排版、clang-tidy 风格、排版基线、增量取集 | 不感知测试，不跑内存检查；只会在编译数据库缺失或过期时，按项目自己声明的 `BUILD_COMMAND` 构建一次 |

`ci` 的可执行阶段固定为 `boundary / clean / build / build_test / run_test / memcheck`；`boundary` 是只读的纪律检查，排在构建之前；
`standardize` 的动作为 `style` / `format` / `check`。

## 三、目录约定

| 位置 | 谁产生 | 谁消费 |
| --- | --- | --- |
| `BUILD_DIR/`、`TEST_BUILD_DIR/` | `ci` 的构建阶段 | 两侧（各自配置里给出） |
| `CI_LOG_DIR/<stage>.log` | `ci` | 人 |
| `<构建目录>/compile_commands.json` | 构建系统本身（cmake 自己导出；qmake 工程由 `compiledb` 解析） | `standardize` |
| `.cxx_ci/` | 两侧各自的状态目录 | 各自 |

`ci` 的 `clean` 阶段保留 `compile_commands.json` 与日志目录：前者是另一个
项目的输入，后者是诊断证据，都不随构建产物清除。

`ci` 在 cmake 构建时传 `-DCMAKE_EXPORT_COMPILE_COMMANDS=ON`：这是构建系统
自己的选项，产出的是标准格式的文件，不是对 clang 的依赖。

## 四、登记清单

两侧各有一份清单，**互不读取、互不解析**：

| 项目 | 文件 | 条目 |
| --- | --- | --- |
| `ci` | `ci.xfail` | `[test] 用例名`、`[memcheck] 符号前缀` 或 `sig:签名哈希` |
| `standardize` | `standardize.xfail` | `[style] 文件:行:检查名`（行可省略） |

两份清单都是纯文本，一行一条，`#` 起注释，行尾可写 `@YYYY-MM-DD` 作复核
到期日，分段头可省略（按形状推断）。细节见各自的 README。

## 五、退出码

各自定义，不要求一致：

| 项目 | 码 | 含义 |
| --- | --- | --- |
| `ci` | 0 | 全部阶段通过 |
| | 非 0 | 阶段失败；高 8 位是错误类型（用法/环境/构建/测试/内存/超时/中断/不可用） |
| `standardize` | 0 | 无需改动 |
| | 1 | 有待改动或检查未通过 |
| | 2 | 工具或配置错误（如缺 clang、配置缺失） |

`ci` 的 `KIND_SKIPPED`（10）表示"阶段在当前机器上不可用"，既不是通过也不是
失败；汇总里单列。

配置声明的 `CONTRACT_VERSION` 与所用工具不一致时，两侧入口都在加载配置阶段
退 2（`ci` 归入环境类，`standardize` 归入工具/配置错误）。

## 六、明确不共享的东西

- **协议行**：`#diag` / `#fail` / `#sum` 只存在于 `ci` 内部（阶段脚本与驱动
  之间）。`standardize` 输出人读文本即可。
- **Python 代码**：两侧连错误码、输出函数、配置加载都不共用。允许少量常量
  与字符串重复，换取"改一侧不会碰另一侧"。
- **配置**：两份独立配置，字段各自自足。公共信息（项目根、构建目录）各写
  一份，语义由本文件约定。

## 七、编排

现代机器上想"一把梭"时，用一个只调 CLI、不 import 两侧内部的薄脚本：

```sh
python3 ci/ci.py                       # 基础流程门禁
cxx-standardize check                  # 排版与风格门禁
```

编排脚本只依赖本文件的 CLI 契约；一旦它 import 了任一侧的内部模块，就构成
新的耦合，评审时应当拒绝。

## 八、登记项：两侧共同的配置语义

两侧不共享代码，但凡"对同一份用户工程做同一判定"的配置项，语义必须由本文件
钉住，避免两侧各自漂移。登记在这里的项，两侧的读写入口都要遵守：

### `SOURCE_ENCODING`（两侧同名同义）

工具会改写用户源码，因此**任何写文件的路径都必须先判定编码，判不出就不写**。
两侧各有一个源码读写入口（`ci/lib/source.py`、`standardize/src/cxx_standardize/
source.py`，互不 import，为平行实现），判定顺序一致：

1. **BOM** 指明的编码；
2. 否则用 `SOURCE_ENCODING` 严格解码（`errors="strict"`，失败即否定）；
   缺省 `"utf-8"` 表示"只认 UTF-8（含 BOM）"；
3. 写了 `"auto"` 才启用 `SOURCE_ENCODING_CANDIDATES`，逐个严格试；
   **候选列表缺省为空**——不猜 GBK，猜错比跳过危险得多；
4. 全失败即编码未判定：该文件**只读**，检查照报、改写跳过并说明原因，
   退出码按"有文件未完成"计（不是 0，也不是失败判卷）。

判定出编码后，写回按原编码 strict 编码，编码不下（例如 GBK 文件里出现 GBK
表示不了的新字符）就拒绝写并指出 offending 行，绝不写一半。排版/改名意图之外
的字节不变：编码、换行符（CRLF 保持 CRLF）、BOM、文件末尾换行逐项还原。

`SOURCE_ENCODING_CANDIDATES` 缺省为空、以及"不猜编码"这条，是两侧共同的约定，
任一侧放宽都视为契约变更。

### 机器可读报告（`--report` / `--report-format`，两侧同形状）

两侧各有一个报告出口，产出**同一份 JSON schema**（不共享代码，为平行实现）。
报告是**旁路产物**：只搬运阶段已算出的结论，不在 writer 里新增任何判定分支；
写报告自身失败（目录不可写、格式非法）只降级为警告，**不改变门禁结论与退出码**。
`report_version` 自管 schema 版本，与工具版本、契约版本分开演进。

- 参数：`--report[=PATH]`（`-` 或缺省值打到标准输出，多格式并列时顺序输出）、
  `--report-format=json|junit|sarif`（可逗号并列，缺省 json）；多格式写文件时对
  各格式加后缀（`.json` / `.xml` / `.sarif`）。
- 顶层字段：`report_version`、`tool`（`ci` / `standardize` / `contract`——各侧只填
  自己那一格，另一侧留空）、`environment`（`python` / `clang_format` /
  `clang_version`）、`config_digest`（关键配置取值）、`baseline`
  （`format_fingerprint` / `xfail_file` / `xfail_count`）、`stages`。
- 阶段字段：`name`、`status`（`passed` / `failed` / `skipped`——跳过不塌成通过）、
  `seconds`、`timed_out`、`diagnostics`、`failures`、`summary`、`skipped_reason` / `unfixable`、
  `log`。
- 诊断条目：`level`、`file`、`line`、`column`、`message`、`check`、`owner`
  （`project` / `external`）、`count`、`xfail`；失败条目：`case`、`reason`、`xfail`。
- writer 映射规则：JUnit 的 `<skipped message>` 承载三态、只承载逐例失败（诊断归
  SARIF），`owner != project` 的诊断不进 JUnit；SARIF 的每条诊断带 `ruleId` 与
  `region`，用 `partialFingerprints`（文件 + 检查名 + 消息摘要）保证跨运行可比，
  `xfail` 记为 `baselineState: "unchanged"` + `properties.knownIssue = true`。
- 报告里的诊断集合必须与终端展示的那一份**一一对应**，不得是第二套判定。
- `standardize` 侧把 style 诊断（含命名诊断）、"需人工"文件清单（承载在
  `failures`）与无自动修复计数（进 `summary`）装进 `stages`；本侧没有"跳过"档，
  `status` 只取 `passed` / `failed`。

## 支持面（SUPPORTED_PLATFORMS）

两侧各有一份 `platform.py`（平行实现，不互相 import），判定与措辞必须一致：

| 级别 | 平台 | 能力 |
| --- | --- | --- |
| `full` | GNU/Linux | 全部能力（含 valgrind / ASan 内存检查） |
| `limited` | macOS / BSD | 排版与风格可用；内存检查标 `[SKIPPED]` 并说明原因 |
| `unsupported` | 其余（含 Windows 原生、Cygwin/MSYS） | 两个入口**在做任何事之前**以退出码 2 退出 |

判定探测优先于查表（系统名 + 实际能否跑出工具版本），避免发行版命名差异误判。
两侧由 `test_platform_parity.py` 的对照测试钉住不漂。

`CONTRACT.md` 另登记两项配置：`SOURCE_ENCODING`（源码编码判定，缺省只认 UTF-8）
与 `CLANG_PREFER`（`wheel`/`system`/`error`：pip clang 包与系统 clang 谁优先）。
