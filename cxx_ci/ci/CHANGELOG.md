# ci 变更记录

本文件记录 `ci/`（门禁项目）**使用者可观察的行为变化**：退出码、结论行、判定
口径、配置字段的增删与默认值、`--version` 输出形状。纯内部重构不在此列——它不
改变判定结果，由 `ci/tests/` 守住。

格式：每条写清"哪个入口 / 变化前 → 变化后"，并标注**契约影响**（是否需要与
`standardize` 侧同步）。按发生先后倒序排列。

## 未发布

### 分类索引（Added / Changed / Fixed / Removed）

上面每条按"哪个入口、变化前 → 变化后"写；这里按四类归一次口，
方便"这次升级动了什么"一眼扫完。**契约影响**一列指的是"是否要让另一侧同步"。

| 类别 | 本次条目 | 契约影响 |
| --- | --- | --- |
| Added | `ci.py --version`；`init` 探测生成配置；`doctor` 只读自检；`--help-config`；`migrate-config`；`--report` / `--report-format`（json/junit/sarif）；`FROZEN` 禁改区；`SOURCE_ENCODING` / `SOURCE_ENCODING_CANDIDATES`；`lib/source.py` 源码读写入口；`lib/platform.py` 平台支持面；`lib/report.py` 报告 schema | 是（FROZEN / SOURCE_ENCODING / 报告 schema 三项已登记进 CONTRACT.md） |
| Changed | 配置项元数据化：`config.py.example` 与 `--help-config` 由 `CONFIG_METADATA` 生成，不再手写；契约校验下移到配置加载（配置里写了 `CONTRACT_VERSION` 就必须与工具一致，否则退 2）；`clean` 跳过禁改区内的产物并计数；内存检查前置校验改用影子构建后的真实程序；`ASAN_COMMAND` 推导同时接受 `BUILD_DIR` 与 `TEST_BUILD_DIR` | 是（契约版本 3） |
| Fixed | 非 UTF-8 源码不再被静默写坏（读不出编码即只读）；BOM 只写一次；CRLF 保真；多份构建文件同层并列时不再静默取最浅；`.pri` 只工程不再只报"没找到"；报告不再自己重判 xfail（改为搬运阶段发的 `#xfail`） | 否 |
| Removed | `clean` 不再无差别清空构建树（禁改区内的产物保留）；`run_all.sh` 不再自行比对两侧契约版本（改由两侧入口各自校验） | 否 |

### 新增 `ci.py --version`


- 变化前：`ci.py` 没有 `--version`，问"这是哪一版"只能看代码或 git commit。
- 变化后：`ci.py --version` 打印一行
  `ci <x.y.z> contract <N> python >=<下限>` 后退出（退出码 0）。它在**加载配置
  之前**返回，因此没有配置的工程也能问版本。
- 契约影响：形状与 `cxx-standardize --version` 一致，是跨项目约定，登记在
  `CONTRACT.md`。

### 契约版本校验下移到本侧入口


- 变化前：只有 `run_all.sh` 比对两侧 `CONTRACT_VERSION`；只用 `ci.py`（老机器
  日常门禁）时，配置取自不同代不会被发现。
- 变化后：加载配置时把配置声明的 `CONTRACT_VERSION` 与本工具实现的
  （`lib/version.py`）比对，不一致**直接拒绝执行，退出码 2**，并说明"配置/工具
  取自不同代，请对齐到同一份 CONTRACT.md"。配置不写、或写的不是整数（视为没
  写）时取工具缺省、静默通过。
- `run_all.sh` 退化为纯编排，不再读任一侧内部模块。

### 依赖清单固化


- 新增 [requirements/ci.txt](../requirements/ci.txt)：**空清单 + 头注释**，让
  "本侧零第三方依赖"成为能被 `pip` 与审计看见的事实。内容由
  `tools/export_requirements.py` 生成，任何被加进来的包都视为违反设计。
