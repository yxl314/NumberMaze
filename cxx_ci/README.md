# cxx_ci

两个**独立项目**的集合：一个服务老机器，一个服务现代机器。它们不共享任何
Python 代码，只通过文件与命令行协作——约定见 [CONTRACT.md](CONTRACT.md)。

| 项目 | 环境 | 职责 | 安装 |
| --- | --- | --- | --- |
| [ci](ci/README.md) | Python >= 3.4，零第三方依赖 | 构建、测试、内存检查、门禁编排 | 源码随项目复制即可 |
| [standardize](standardize/README.md) | Python >= 3.11 | 代码排版、clang-tidy 风格、排版基线、增量取集 | `pipx install` |

安装按场景写死在 [INSTALL.md](INSTALL.md)（老机器无网 / 现代机器 / 钉 clang
版本），依赖清单统一在 [requirements/](requirements/) 下，正文不再手抄包名。

> **支持面**：GNU/Linux 全部能力；macOS / BSD 只保证排版与风格（内存检查标 `[SKIPPED]`）；其余平台启动即拒。详见 `CONTRACT.md`。

## 为什么分成两个项目

`standardize` 本就不是给老机器用的，却长期被"必须在 Python 3.4 上可测"的
约束扭曲：整个仓库禁用 f-string、`pathlib`、`typing`、`dataclasses`、
`subprocess.run`，测试矩阵被迫双端。在单包里做分层只是把扭曲挪个位置——
只要两侧在同一份配置、同一套测试里，"顺手共用"就会持续把现代侧拉回 3.4。

分开之后：

- `ci` 侧代码必须能在 3.4 上 `import` 与运行（标准库之外无依赖）；
- `standardize` 侧不为 3.4 做任何妥协，可用 3.11+ 特性与 pip 装的依赖；
- 两侧各自独立目录、独立配置、独立测试、独立版本；
- 跨项目只有文件与 CLI 契约，没有 Python 接口。

代价是少量代码重复（错误码、输出函数、配置加载各写一份）。这是有意为之：
重复的量很小，换来的是"改一侧不会碰另一侧"的真实自由。

## 常用命令

```bash
# 老机器：基础流程门禁
python3 ci/ci.py

# 现代机器：排版与风格门禁
cxx-standardize check

# 现代机器：整理代码
cxx-standardize style --apply
cxx-standardize format --apply

# 现代机器：一把梭（薄编排，只调 CLI；需要先 pipx install ./standardize）
./run_all.sh

# 跑两个项目的测试（各自的环境不同，因此分两次）
./run_tests.sh
```

## 目录

```
cxx_ci/
├── CONTRACT.md           跨项目契约，唯一需要共同遵守的文档
├── README.md             本文件
├── run_all.sh            薄编排：顺序调用两个入口，不 import 任何一侧内部
├── run_tests.sh          两个项目各自的测试
├── ci/                   项目一：Python >= 3.4
└── standardize/          项目二：Python >= 3.11
```

`run_all.sh` 是唯一的"跨项目代码"，它只调命令行。评审时要守住这条：一旦它
import 了任一侧的内部模块，两个项目就又绑在一起了。

## 自动修到什么程度（可直接当接入依据）

| 类别 | 自动程度 | 判据 / 收尾 |
| --- | --- | --- |
| 排版（clang-format） | **全自动** | `--apply` 一次修完；注释按行尾原间距平移，`protect-column` 下要求列号不变 |
| 非命名风格（clang-tidy） | **全自动 + 构建判卷** | 逐 TU 只读导出 → 合并去重 → 一次原子写入 → 末尾跑 `VERIFY_COMMAND`，不过整体回滚；收尾自动排版 |
| 命名（口径表驱动） | **全自动** | 位置用 clang-tidy 的，文本按 `NAMING_PREFIX` 算；跨 TU 一致；判卷不过整体回滚并列出需人工的文件 |
| clang-tidy 不提供修复块的检查 | **不自动** | `--export-fixes` 里 `Replacements` 为空。`--apply` 会点名报出并返回非零，绝不报"全部符合" |
| 宏生成的符号 | 规范豁免 | `.clang-tidy` 里 `IgnoreMacros` + `NAMING_EXEMPT_KEYS` |

一条命令跑完三件事：

```bash
python3 cxx_ci/standardize/standardize.py --config config/standardize.py --apply
```

缺省动作就是 `all`（排版 + 风格 + 命名）。只看不改去掉 `--apply`。
