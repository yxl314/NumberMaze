# standardize 变更记录

本文件记录 `standardize/`（排版与风格项目）**使用者可观察的行为变化**：退出码、
结论行、判定口径、配置字段的增删与默认值、`--version` 输出形状、运行依赖。
纯内部重构不在此列——它不改变判定结果，由 `standardize/tests/` 守住。

格式：每条写清"哪个入口 / 变化前 → 变化后"，并标注**契约影响**（是否需要与
`ci` 侧同步）。按发生先后倒序排列。

## 未发布

### 分类索引（Added / Changed / Fixed / Removed）

上面每条按"哪个入口、变化前 → 变化后"写；这里按四类归一次口。
**契约影响**一列指的是"是否要让另一侧同步"。

| 类别 | 本次条目 | 契约影响 |
| --- | --- | --- |
| Added | `--version`；`init` 探测生成配置；`doctor` 只读自检；`migrate-config`（并保留 `--migrate-config` 别名）；`--report` / `--report-format`（json/junit/sarif）；`CLANG_FORMAT_BIN` / `CLANG_TIDY_BIN` / `CLANG_PREFER`；`SOURCE_ENCODING` / `SOURCE_ENCODING_CANDIDATES`；`config/source.py` 源码读写入口；`config/platform.py` 平台支持面；`config/paths.py` 的 `is_frozen` / `scope_of`（与 ci 侧同名同实现） | 是（SOURCE_ENCODING / CLANG_PREFER / FROZEN / 报告 schema 已登记进 CONTRACT.md） |
| Changed | 契约校验下移到配置加载（写了 `CONTRACT_VERSION` 就必须与工具一致，否则退 2）；配置说明集中在 `config.py.example` 的分节注释；诊断带上 owner（归属，与 ci 侧同一套 `scope_of`）；`style` 三入口共享同一份前置准备（派生文件三个入口都生成） | 是（契约版本 3） |
| Fixed | 非 UTF-8 源码不再被静默写坏（读不出编码即只读、退出码非 0）；BOM 只写一次（`utf-8-sig` 编码器自带 BOM，曾写出两个）；CRLF 保真；行尾注释 + 独立左括号的形状不再被排版永久拒绝；`--apply` 不再对改不动的诊断报"全部符合"；`--apply-naming` 单用不再静默空转 | 否 |
| Removed | 不再保留裸的 `read_bytes()+encode("utf-8")` 写入路径（一律过 `source.py`）；不再由报告重判 xfail（改为搬运 ci 侧发的 `#xfail`） | 否 |

### `--version` 改为统一形状


- 变化前：逐行打印 `python:` / `executable:` / `config:` / `cwd:`，再 `version:`、
  `contract:`。
- 变化后：打印一行 `cxx-standardize <x.y.z> contract <N> python >=<下限>` 后退出
  （退出码 0）。环境细节不再进 `--version`——那属于 `doctor`。
- 契约影响：形状与 `ci.py --version` 一致，是跨项目约定，登记在 `CONTRACT.md`。

### 契约版本校验下移到本侧入口


- 变化前：只有 `run_all.sh` 比对两侧 `CONTRACT_VERSION`；只用 `cxx-standardize`
  时，配置取自不同代不会被发现。
- 变化后：加载配置时把配置声明的 `CONTRACT_VERSION` 与本工具实现的
  （`cxx_standardize/version.py`）比对，不一致**直接拒绝执行，退出码 2**，并说明
  "配置/工具取自不同代，请对齐到同一份 CONTRACT.md"。配置不写、或写的不是整数
  （视为没写）时取工具缺省、静默通过。
- `run_all.sh` 退化为纯编排，不再读任一侧内部模块。

### 运行依赖清单固化


- 新增 [requirements/standardize.txt](../requirements/standardize.txt)、
  `standardize-clang17.txt`、`dev.txt`，与 `pyproject.toml` 同源生成（由
  `tools/export_requirements.py`），一条测试钉住不漂。
- `PyYAML` 依赖补上界：`>=6.0` → `>=6.0,<7`，避免大版本 surprise。

### `style --apply` / `style --record` 在派生文件未就绪时出声


- 变化前：`--record` 与 `--apply` 都不生成派生文件（`moc_`/`ui_`/`qrc_`）。编译
  数据库已存在但构建树里没有这些文件时，含 `#include "X.moc"` 的翻译单元会被
  clang-tidy 判成编译错误、该 TU 的 fix-it 一并被抑制。此时 `--record` 会**漏登记**
  这些诊断，随后 `style`（它会 prepare）把它们报成"新增违规"——用户没改代码，
  门禁却红了；`--apply` 则在整个过程里"什么都没修"，却只打印"全部文件已符合
  风格"并返回成功。
- 变化后：
  1. 三个入口（`style` / `style --record` / `style --apply`）共享同一段前置准备，
     都会生成派生文件；同一时刻、同一配置下三者看到的待分析集合一致。
  2. 派生文件生成失败时，`--record` 与 `--apply` 在 stderr 打印
     `派生文件未就绪，结果不完整；含 #include "X.moc" 的翻译单元可能未被完整分析`。
  3. 在 `XFAIL_STRICT = True` 时，上述情形使 `--record` / `--apply` 返回
     `Code.STYLE_FAILED`（退出码 1）；非严格时退出码不变，但不再打印"全部文件已
     符合风格"——不允许"0 个 TU 被分析"与"全部 TU 干净"给出相同输出。
  4. `--no-prepare` 现在对三个入口都生效（此前只对 `style` 生效）。

- 判定结果不变：这条只影响**结论行与退出码**，不改变诊断集合、xfail 分类或修复
  行为。已由 `standardize/tests/test_style_run.py::TestIncompleteIsNotEmpty` 钉死。
