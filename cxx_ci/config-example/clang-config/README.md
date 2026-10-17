# clang 排版与风格配置示例

一套**可直接开跑**的 `.clang-format` + `.clang-tidy`，以及配套的命名口径表。

它不是玩具：`standardize` 的命名元检查会现场推导 `.clang-tidy` 应为何并逐项
比对，这份示例与 `naming-tables.py` 配在一起能**一次通过**——所以它是一份真实
可用的规范实例，改动它可以照常跑门禁。

## 三份文件的关系

| 文件 | 内容 | 谁读它 |
|---|---|---|
| `.clang-format` | 排版规范（缩进、大括号、对齐、断行、注释） | `standardize` 的排版阶段（配置项 `CLANG_FORMAT_CONFIG`），也可 `clang-format --style=file` 直接跑 |
| `.clang-tidy` | 检查开关 + 命名规则（`CheckOptions`） | `standardize` 的风格阶段（配置项 `CLANG_TIDY_CONFIG`），也可 `clang-tidy -p <build-dir> <file>` 直接跑 |
| `naming-tables.py` | 命名口径表（`NAMING_*`） | 粘进 `config/standardize.py`；它是命名规范的**唯一事实源** |

口径表与 `.clang-tidy` 必须一致，这一点由工具守着：`standardize` 从口径表现场
推导出 `.clang-tidy` 应有的键值再逐项比对，偏离即红。**改口径只改口径表**，
不必同步两份文本。

## 怎么用

```bash
# 1. 复制两份 clang 配置到工程根
cp config-example/clang-config/.clang-format config-example/clang-config/.clang-tidy .

# 2. 生成配置，再把 naming-tables.py 的片段粘进 config/standardize.py 的
#    "命名口径"一节
python3 cxx_ci/standardize/standardize.py init --config config/standardize.py

# 3. 先看当前有多少不合规（只读）
python3 cxx_ci/standardize/standardize.py check --config config/standardize.py
```

接入存量工程时先登记再收紧，否则第一次跑就是几百条：用 `--record` 把当前诊断
冻结成基线（`standardize` 的登记清单），此后只有**新增**违规判红。

## 改这份示例之前要知道的几件事

**命名规则为什么长这样。** 前缀是**位置**的标记，不是修饰：`g_` 告诉你"这是
全局、改它要看过所有调用点"，`s_` 告诉你"这是函数内 static、第一次调用时
才初始化"。所以 `m_/t_/v_` 分得细是有意的——类成员、结构体字段、参数在改动
时的风险面不同。

**四类 kind 一个都不能少。** clang-tidy 把 `T`、`const T`、`T*`、`const T* const`
分成四个独立的 kind（`LocalVariable`/`LocalConstant`/`LocalPointer`/
`LocalConstantPointer`，全局与 static 同理）。少配一类，那一类声明的违规
**根本不报**，而且不会有任何提示。示例里每一组都写全了。

**几个 kind 不在 `--dump-config` 输出里。** `Static*` 那组就是这样，能力只能
靠行为探针确认（`standardize` 的 `probe` 干这件事）。看到 `dump-config` 里没有
不要以为配错了。

**`IgnoreMacros` 不可关。** 宏体生成的名字天生带各类前缀，不豁免就没有一条
前缀规则能落地。

**`MacroDefinitionCase` 刻意不写。** 写了会被命名元检查判红（它记在
`NAMING_EXEMPT_KEYS` 里）。

**局部的 `IgnoredRegexp` 不要手改。** 它是按 `NAMING_LOCAL_BAN` 推导出来的，
抄错一个分支就会出现"某些名字永远不报"的后门。`POSIX ERE 没有先行否定`，所以
正则写成三段分支，这不是绕远路。

**`SortIncludes: false` 是取舍。** 自动排序会动到有顺序含义的包含（生成头、
条件包含）。要自动排序就改成 `true`，但先确认没有依赖顺序的包含。

## 按工程调整的地方

这几处是**故意留成示例值**的，换一个工程必须动：

| 位置 | 示例值 | 说明 |
|---|---|---|
| `.clang-tidy` 的 `HeaderFilterRegex` | `Source/.*` | 换成只覆盖本仓可改源码的范围。冻结库与第三方头要从这里排除——不只是降噪：`--fix` 会把改名写进它们 |
| `.clang-tidy` 的 `NamespaceCase` | `CamelCase` | 命名空间不在口径表里（不要求前缀），按仓库现状定 |
| `.clang-tidy` 的各 `*IgnoredRegexp` | 若干历史风格 | 都是"改名会直接坏掉"或"存量待清"的豁免。**每一条都该有理由**，不要为了消红而加 |
| `.clang-format` 的 `ColumnLimit` | `120` | 行宽 |
| `naming-tables.py` 的 `NAMING_LOCAL_BAN` | `gmstv` | 局部禁用前缀集；改了它，`.clang-tidy` 里的局部正则要跟着重算（工具会推导，别手抄） |

## 实测：这套配置会抓到什么

抓过去之后跑一次（clang-tidy 17 + clang-format 17）：

| 样本 | 结果 | 说明 |
|---|---|---|
| 参数 `count`（无 `v_`） | 报违规 | 形参与 lambda 形参都按 `v_` |
| 私有成员 `bad_member` | 报违规 | 需 `m_` |
| **函数内** `static int g_wrong_static` | 报违规 | 需 `s_`；函数内 static 与全局是两个 kind |
| 局部 `int v_bad_local` | 报违规 | 局部禁用 `gmstv` 前缀 |
| **文件级** `static int g_good_global` | 不报 | clang 把文件级 static 归为全局，按规范就该是 `g_` |
| 裸名局部 `int good_local` | 不报 | 局部不要求前缀 |

第二行与第五行合起来是最容易踩的一处：**文件级 static 用 `g_`、函数内 static 用
`s_`**，两者在源码里长得一模一样，区别只在作用域。这不是本工具的取舍，是
clang-tidy 的分类——所以规范照它的分类写。

另外两条不出现在上面的表里，但一样重要：

* `--apply-naming` 只清"去前缀类"的改动，**不会**帮你把 `g_wrong_static` 改成
  `s_wrong_static`。改名一律人工复核——clang-tidy 的 fix-it 会盲目叠加前缀
  （`v_foo` → `_v_foo`），自动套用只会把名字搞坏。
* 门禁（`check`）不受 `--fix` 影响：它只判"当前状态合不合规"。

## 与 ci 侧的关系

排版与风格由 `standardize` 负责（能改工作区），门禁由 `ci` 负责（只读）。
两边靠文件系统与命令行契约协作，配置项名字一致（`CLANG_FORMAT_CONFIG` 等）。
接入方式见仓库根的 `CONTRACT.md` 与 `INSTALL.md`。
