# ci

基础流程门禁：构建、测试、内存检查与阶段编排。跑在老机器上——**Python >= 3.4、
零第三方依赖**（只用标准库）。

风格整理与代码排版属于另一个项目（`standardize/`），本项目不含任何 clang 概念，
也不与它共享 Python 代码。两者的关系与协作方式见仓库根的
[CONTRACT.md](../CONTRACT.md)。

## 目录结构

```
ci/
├── ci.py                 入口
├── stages/               阶段脚本：clean / build / build_test / run_test / memcheck
├── lib/                  实现库（平铺一层，整体 3.4 兼容）
│   ├── errors.py         错误码
│   ├── output.py         输出与编码
│   ├── paths.py          路径与文件收集
│   ├── config.py         配置加载与校验
│   ├── cli.py            命令行骨架
│   ├── tools.py          外部工具与命令执行
│   ├── build.py          构建
│   ├── protocol.py       阶段脚本与驱动层的协议行
│   ├── diagnostics.py    诊断解析与归属
│   ├── testreport.py     测试报告适配器
│   ├── valgrind.py       valgrind 报告解析与归因
│   ├── asan.py           asan 影子构建与双通道判定
│   ├── stage.py          阶段的执行、落盘与展示
│   └── xfail.py          已登记存量问题的清单
├── tests/                单元、集成与冒烟测试
└── README.md
```

---

## 接入一个新工程：两条命令

```sh
python3 ci/ci.py doctor --config config/ci.py    # 只读自检：现在生效的是什么
python3 ci/ci.py --config config/ci.py           # 跑门禁
```

* **`doctor`**：两段一屏内——①环境（Python、构建工具、clang 两件套的实际路径
  与版本、valgrind / libasan、依赖清单、**平台级别与内存检查实探测结果**）；
  ②生效配置（关键取值 + 每项来源：配置文件 / 推导 / 缺省）。
  配置建不起来时也照常给环境段与原因。
首次接入建议把存量登记成基线，门禁当天变绿、以后只对新问题判红：
`cxx-standardize --config config/standardize.py style --record`。

## 支持面

GNU/Linux 全部能力；macOS / BSD 只保证排版与风格（内存检查标 `[SKIPPED]` 并
说明原因）；其余平台启动即以退出码 2 拒绝。级别按 `os.uname()` 的系统名判，
"某项能力能不能用"由实探测决定（装了 valgrind 的 BSD 能用，没装的 GNU/Linux
也能走 ASan）。详见 `CONTRACT.md`。

## 使用

```bash
python3 ci.py                      # 基础流程：clean/build/build_test/run_test/memcheck
python3 ci.py --only build         # 只跑指定阶段
python3 ci.py --skip memcheck      # 跳过指定阶段
python3 ci.py --level verbose      # 透传阶段输出
python3 ci.py --timeout 600        # 单阶段超时秒数
python3 ci.py --no-clean-after     # 保留构建产物
python3 ci.py --config <路径>      # 指定项目配置（缺省见下）
```

配置的位置按这个顺序找：`--config` 给的路径 → 环境变量 `CXX_CI_CONFIG` →
工具自带的 `config/config.py`。`--config` 会被透传给每个阶段脚本，因此两种入口
的行为一致。

风格与排版不在这里，用另一个项目：

```bash
cxx-standardize check              # 排版与风格门禁
```

## 输出契约

三条输出通道，归属固定：

| 通道 | 内容 | 去向 |
| --- | --- | --- |
| 协议行 | `#diag` / `#fail` / `#sum` | 判定来源，按 `--level` 回显 |
| 阶段 stdout/stderr | 完整输出 | 落盘 `CI_LOG_DIR/<阶段>.log` |
| 过程噪声 | 工具自身的进度回显、`make: 已是最新` | 默认只落盘，`--verbose` 才透传 |

因此终端只出现诊断与结论行，噪声仍能在 `CI_LOG_DIR/<阶段>.log` 里查到。

`ci.py --level` 的取值决定回显策略：`quiet` 只在失败时出声，`normal` 只回显
协议行与摘要，`verbose` 透传全量（此时不保证完整落盘）。

## 输出与诊断

成功阶段只回显**结构化诊断**，不做关键字猜测：编译器告警取
`路径:行:列: 级别: 消息` 版式，valgrind 取 `ERROR SUMMARY` 汇总行。因此

- 自带日志系统的被测程序（`|ERROR|`、`|WARN|`）不会被当诊断；
- 工具自身的命令回显、valgrind 标语不会被当诊断；
- 级别取自版式中的位置，不依赖 `warning|error` 这样的词表，`WARN` 之类
  的缩写不会漏，也不会误命中。

每个阶段的完整输出始终落盘到 `CI_LOG_DIR`（缺省 `BUILD_DIR/ci-logs`），
控制台筛掉的内容都能在那里查到。该目录刻意放在构建目录下并让 `clean`
保留：`clean` 会清空构建目录的其余内容，否则摘要给出的路径会变成死链。
整个构建目录通常已在项目的忽略列表里。

诊断展示由四项配置决定：

| 配置 | 取值 | 作用 |
|---|---|---|
| `DIAGNOSTIC_LEVEL` | `off`/`note`/`warning`/`error` | 显示门槛，缺省 `error`；`off` 完全静音 |
| `DIAGNOSTIC_SOURCES` | `project`/`all` | 只显示项目自身的，或连第三方头文件一并显示 |
| `DIAGNOSTIC_IGNORE` | 正则列表 | 命中即静默，匹配原始行与消息 |
| `DIAGNOSTIC_DEDUP` | `True`/`False` | 同一条只显示一次，行尾标注 `[×N]` |

成功时最多再给一行摘要：

```
[     ok] build (2.5s)
    /opt/qt/include/QtCore/qvariant.h:259:25: warning: ... [-Wdeprecated-copy] [×5]
    1 条诊断（合并重复 4 条，过滤 5 条，见 build/ci-logs/build.log）
```

阶段子进程固定跑在 `LC_ALL=C` 下：编译器会把级别词本地化（中文环境下
g++ 输出 `警告：` 而非 `warning:`），诊断解析依赖固定版式，输出语言必须
稳定。阶段脚本自身调用 `common.ensure_utf8_streams()`，中文照常显示；
被测程序若要输出非 ASCII，需要自己处理编码。

归属判定先**把路径归一化成绝对路径**再做 `SCOPE` 前缀匹配。编译器给出的
相对路径是相对它自己的工作目录的，而该目录随构建工具与子工程深度变化
（qmake 影子构建常在 `build/src` 里编译，路径形如 `../../src/x.cpp`）。
归一化按两步走：先按构建根及其下若干层子目录逐个尝试，再退回按后缀在项目
内查找（命中最长的后缀）。跳过这一步，项目自身的告警会被一律判成域外而
静默——实测 1404 条过滤里就藏着项目自己的告警。

## 快速开始

三步，与工程用什么构建系统、叫什么名字无关：

```bash
# 1. 把 ci/ 拷进项目（或放到项目旁边），再复制一份配置
cp config-example/ci_config.py config/ci.py

# 2. 改这几项：项目根、构建工具、构建文件、测试指令
#    ROOT / BUILD_TOOL / BUILD_FILE / TEST_COMMAND / TEST_WORKDIR

# 3. 跑
python3 ci/ci.py
```

`ROOT` 缺省是 `ci/` 的父目录（工具与项目同级时不用改）；构建文件与构建目录
按 `BUILD_TOOL` 自动探测，探测到什么会打出来。项目没有测试或不做内存检查时，
把对应的项留空即可——那些阶段会标 `[SKIPPED]` 并说明缺什么，既不算通过也不算
失败；也可以用 `STEPS_DEFAULT` 把它们排除在默认全量之外。

配置不纳入版本管理，各人本地改各自的。每个阶段脚本都能单独跑：

```bash
python3 ci/stages/build.py --config /path/to/config.py
```

## 配置

**必需项只有两个**：`ROOT`（项目根）与 `BUILD_TOOL`（构建工具）。这两个猜不
出来，其余都能推：

| 没写的项 | 怎么办 |
| --- | --- |
| `BUILD_FILE` | 按 `BUILD_TOOL` 在 `ROOT` 下找 `CMakeLists.txt` 或 `*.pro`；找不到直接报错并说清该写什么，不猜 |
| `BUILD_DIR` | 取 `ROOT/build` |
| `TEST_BUILD_FILE` / `TEST_BUILD_DIR` | 与项目共用同一个构建文件与构建目录 |
| `MEMCHECK_COMMAND` | 与 `TEST_COMMAND` 相同 |
| `TEST_COMMAND` / `TEST_WORKDIR` | 视为"这个工程不跑测试"，`run_test` 与 `memcheck` 标 `[SKIPPED]` 并说明缺什么 |
| `CXX_COMPILER` / `CXX_STANDARD` | 不传，由构建工具自己的缺省决定 |
| `BUILD_TOOL_BIN` | 自动探测（兼容 `cmake3` 这类带版本后缀的命名） |

探测到什么会随配置的提示打出来，不静默生效。全部配置项见
`config-example/ci_config.py`，每一项都有说明。几个要点：

- `SCOPE` 是"哪些代码算本项目"的单一来源，诊断归属、style 与 format 的
  处理范围、内存归因都走它。旧名 `WHITELIST`/`BLACKLIST` 保留可用，会
  提示一次。
- 路径类配置项一律同时接受 `Path` 与 `str`：`MEMCHECK_SKIP_TARGETS`、
  `MEMCHECK_IGNORE_OBJECTS`、`SCOPE` 等都能直接写 `ROOT / "build" / "tests"`，
  不必记住哪个是特例。
- `TEST_ENV` / `MEMCHECK_ENV` 注入测试与内存检查的子进程环境，GUI 项目
  无头运行写在这里，比塞进 `TEST_COMMAND` 干净。
- 阶段子进程固定跑在 `LC_ALL=C` 下：编译器会把级别词本地化（中文环境下
  g++ 输出 `警告：` 而非 `warning:`），诊断解析依赖固定版式。

## 协议行

阶段脚本与驱动层之间有一条机器可读通道，人读仍是普通文本：

```
#diag error: <文件>:<行>:<列>: <消息>
#fail: <用例> <原因>
#sum: <一行摘要>
```

生成侧知道自己在跑什么（编译器、测试框架、valgrind），判定因此来自结构
而不是子串猜测。驱动层只认协议行；没有协议行时退回解析编译器版式，并在
行尾标注 `(启发式)`，让读者知道这条结论有多可信。

`config/config.py` 是一个 Python 模块，路径一律写成 `ROOT / "..."`：

| 项 | 说明 |
| --- | --- |
| `ROOT` | 被 CI 的项目根，缺省是 `cxx_ci` 所在的目录 |
| `BUILD_TOOL` / `BUILD_TOOL_BIN` | `cmake`、`qmake` 或 `qmake6`；可执行文件名留空则自动探测 |
| `BUILD_FILE` / `BUILD_DIR` | 构建文件与输出目录 |
| `CXX_COMPILER` / `CXX_STANDARD` | 编译器与语言标准 |
| `TEST_BUILD_FILE` / `TEST_BUILD_DIR` | 测试构建文件与输出目录 |
| `TEST_COMMAND` / `TEST_WORKDIR` | 测试指令与工作目录 |
| `WHITELIST` / `BLACKLIST` | 处理范围，黑名单优先 |
| `MEMCHECK_COMMAND` | 内存检查执行的指令，缺省同 `TEST_COMMAND` |
| `MEMCHECK_TOOL` | 内存检查路线：`valgrind`（缺省）或 `asan` |
| `MEMCHECK_SUPPRESSIONS` | valgrind 抑制文件，不存在时只警告 |

## 阶段与工具

`--only` / `--skip` 对下表每个阶段都可用。

| 阶段 | 工具 | 说明 |
| --- | --- | --- |
| clean | — | 清空构建目录，保留编译数据库与日志目录（前者是另一个项目的输入），拒绝删除项目外的路径 |
| build | cmake / qmake | 配置并编译 |
| build_test | 同上 | 编译测试 |
| run_test | 配置的指令 | 逐例结果由报告适配器解析；多套件 runner 的汇总逐段累加，有失败即判失败 |
| memcheck | valgrind / asan | 按 `MEMCHECK_TOOL` 选路线，见「内存检查」 |


`STEPS_DEFAULT` 决定 `ci.py` 默认跑哪些阶段（`None` 表示上表全部）。

阶段真正跑起来后才发现工具缺失时，不会被判红：标成 `[SKIPPED]` 并给出原因，
汇总里也单独列出——**跳过不等于通过**，它只是不把"机器上没装构建工具或
valgrind"错报成"代码有问题"。

## 测试报告

`run_test` 不只看退出码：逐例失败由报告适配器解析后发成 `#fail`，因此
**runner 退出 0 但有失败用例**也会判失败——这是最危险的一类静默漏报。

| 适配器 | 指纹 |
| --- | --- |
| `gtest` | `[==========]`、`[ RUN ]` |
| `regex` | 自研 runner，用 `TEST_FAIL_PATTERN` / `TEST_TOTAL_PATTERN` |
| `exit-code` | 只看退出码（旧语义） |

`auto` 按上表顺序试探首个命中的指纹。**汇总逐段累加**：多套件 runner 会把
多个测试可执行文件串起来跑，每跑完一个打印一段汇总，只取首段会把
用例总数报成单个可执行文件的规模（实测 29 段 245 例被报成 11 例）。汇总行
给出用例数与套件数：

```
共 245 例（29 套件），失败 0 例
```

`exit-code` 适配器不受影响，仍返回空结果。

## 内存检查

内存检查有两条路线，由 `MEMCHECK_TOOL` 选一条：

| 路线 | 做法 | 适合 |
| --- | --- | --- |
| `valgrind`（缺省） | 结构化报告按调用栈归因 | 合入门禁的深度扫描 |
| `asan` | 影子构建 + ASan/LSan 双通道 | 日常：快一个数量级 |

两条互补而非替换："日常 asan / 合入 valgrind" 用 `STEPS_DEFAULT` 或两份配置
表达即可，不需要额外机制。

### valgrind 路线

valgrind 出结构化报告（`--xml`），工具按调用栈把每条错误归到项目、忽略集
或系统库，**成败由归因结果决定**而不是"有没有任何一条错误"。

| 配置 | 作用 |
| --- | --- |
| `MEMCHECK_SCOPE` | `project` 只把落在 `SCOPE.project` 的算失败；`all` 全量计入（旧语义） |
| `MEMCHECK_IGNORE_OBJECTS` | 要忽略的归属对象（可执行文件或库名），按精确路径比较 |
| `MEMCHECK_IGNORE_SYMBOLS` | 要忽略的**符号前缀**（如 `NormalButton::`），静态链接下唯一可用的依据 |
| `AUTO_IGNORE_ROOTS` | 自省外部代码根：cmake 缓存前缀、qmake 的 `QT_INSTALL_LIBS`、编译数据库里的库目录 |
| `MEMCHECK_IGNORE_KINDS` | 整体忽略的泄漏类别，缺省 `still-reachable` + `possibly-lost` |
| `MEMCHECK_REPEAT` | 重复运行次数，取**交集**判定：只有每次都判域内的才算失败 |
| `MEMCHECK_SKIP_TARGETS` | 命中的测试目标整体不跑，汇总标 `skipped` |
| `MEMCHECK_SUPPRESSIONS` | **可选精修**，不再是接入前提 |

系统库前缀（`/usr/lib*`、`/lib*`）无条件并入忽略集，不依赖探测成功。
被测程序自身的帧算项目产物——它在构建目录里，而构建目录在 `SCOPE.excluded`
中，只看源码范围会把真实泄漏判成域外而放过。

**静态链接的 vendored 代码只能靠符号归因。** 库链进可执行文件后，帧的归属
对象就是可执行文件本身，路径级忽略对它完全无效——那个路径正是项目自己的
产物。实测某静态构建 35 条域内问题里 13 条同属一个 vendored 符号前缀，
81% 的域内判定是假阳性。这时配 `MEMCHECK_IGNORE_SYMBOLS`：

```
域内问题 1 处 | 忽略 158 处（外部 155 / 系统 2 / 类型 1）
  域内条目按符号前缀聚合 Top 4: NormalButton:: 13、QMetaObject:: 2、Menu:: 1、TextMessageBox:: 1
  - [Leak_DefinitelyLost] 24 bytes in 1 blocks are definitely lost（落在项目根内: …/app）（位置: …/app Menu::make_menus()） [sig:1a2b3c4d]
```

聚合行先点出"大头是谁"，`[sig:…]` 是这条栈的稳定签名（不含字节数），可用它
在 `ci.xfail` 里精确登记某一条。共享库形态才能靠路径归因。

### 判定抖动与收敛

被测程序含定时器或异步事件循环时，valgrind 的泄漏分类**本身就会漂移**：
"堆上是否仍残留指向该块的指针"随时序变化，同一份二进制两次运行的域内条数
能差两三成，门禁会红绿翻脸。两道收敛：

1. **把漂移类别移出失败判定**。`MEMCHECK_IGNORE_KINDS` 缺省已含
   `possibly-lost`（漂移的主源），确凿的泄漏由 `definitely-lost` /
   `invalid-*` / `uninit-*` 兜住。要恢复旧口径就把这一项显式写窄。
2. **重复运行取交集**。`MEMCHECK_REPEAT = 2` 时跑两遍，只有**每次都判域内**
   的签名才算失败，用可复现的失败集换掉单次快照：

   ```
   重复 2 次域内签名 79 / 100 → 交集 75 | 域内问题 75 处 | 忽略 296 处（…） | 明细: build/ci-logs/memcheck.xml
   域内条目按类别: definitely-lost 61、invalid-read 3、uninit 11
   域内条目按符号前缀聚合 Top 5: TableView:: 52、NormalButton:: 22、…
   ```

   计数按"签名"（调用栈）而不是错误条数：跨运行可比的是栈。代价是耗时成倍，
   因此缺省关闭。

3. **静态链接 vendored 库的工程：两项一起配。**

   ```python
   MEMCHECK_REPEAT = 2
   MEMCHECK_IGNORE_SYMBOLS = ["TableView::", "NormalButton::", "HToolBar::"]
   ```

   只配 `MEMCHECK_REPEAT` 时域内集合仍会变（每次都能复现的冻结库符号还在），
   只配 `MEMCHECK_IGNORE_SYMBOLS` 时"这次没复现"的条目仍会让红绿抖动。前缀
   清单直接从摘要的 `域内条目按符号前缀聚合 Top N` 一行抄。

摘要的这三行都会进控制台（`normal` 及以上），逐条明细留在
`CI_LOG_DIR/memcheck.log`——面对几十条域内问题时，先看清"哪一类、哪个符号
前缀"才谈得上收敛。

### asan 路线

asan 在**影子目录**里单独编译测试工程（缺省 `BUILD_DIR/tests_asan`），
**不编译主产物**：日常跑一次不会把合入门禁用的主构建树也换成带净化器的版本。
注入形式按构建工具：

| 构建工具 | 注入 |
| --- | --- |
| qmake | `QMAKE_CXXFLAGS+=<ASAN_CXXFLAGS>` 与 `LIBS+=<ASAN_LDFLAGS>`；`LIBS` 那一项排在命令行末尾，asan 运行库才会落在 `LIBS` 尾部 |
| cmake | `-DCMAKE_CXX_FLAGS=<ASAN_CXXFLAGS>` 与 `-DCMAKE_EXE_LINKER_FLAGS=<ASAN_LDFLAGS>` |

测试指令缺省由 `MEMCHECK_COMMAND` 推导：把其中的 `BUILD_DIR` 前缀换成
`ASAN_BUILD_DIR`，参数原样保留；要另指别的程序再写 `ASAN_COMMAND`。

运行时分两个通道，用同一个二进制：

| 通道 | 跑什么 | 环境 |
| --- | --- | --- |
| A（内存错误） | 全量用例 | `LD_PRELOAD=<asan 运行库>`、`ASAN_OPTIONS=detect_leaks=0` |
| B（泄漏扫描） | 加 `ASAN_LEAK_ARGS`（如 `--no-gui`），只跑无 GUI 逻辑用例 | `ASAN_OPTIONS=detect_leaks=1`、`LSAN_OPTIONS=suppressions=<ASAN_SUPPRESSIONS>` |

运行库路径由 `CXX_COMPILER -print-file-name=libasan.so` 取得，前置到
`LD_PRELOAD`：环境注入库会抢 `DT_NEEDED` 首位，不前置时拦截器装不上，asan
只会静默放行。找不到运行库时阶段标 `[SKIPPED]`，不判红。

**为什么是双通道**：常驻注入软件以 ptrace 附着全部进程时，LSan 的收尾扫描遇到
Qt GUI 事件循环会被打到 fatal——那是环境冲突，不是被测代码泄漏。把泄漏扫描
限制在无 GUI 用例上就绕开了这个冲突面；use-after-free、越界、双释这类错误由
拦截器在发生当场抓，不依赖 ptrace，环境鲁棒。

`ASAN_SUPPRESSIONS` **必须存在**，否则阶段直接终止（`ASAN_SUPPRESSIONS_MISSING`），
不会去编译或运行二进制：Qt 对象树所有权产生的存量"泄漏"要靠它压掉，没有清单
的裸跑只会把它们全报成域内问题，通道 B 当场失去意义。

| 配置 | 作用 |
| --- | --- |
| `MEMCHECK_TOOL` | `valgrind`（缺省）或 `asan` |
| `ASAN_BUILD_DIR` | 影子构建目录，缺省 `BUILD_DIR/tests_asan` |
| `ASAN_COMMAND` | asan 路线的测试指令，缺省由 `MEMCHECK_COMMAND` 推导 |
| `ASAN_CXXFLAGS` / `ASAN_LDFLAGS` | 注入影子构建的编译与链接参数 |
| `ASAN_LEAK_ARGS` | 通道 B 追加给二进制的参数（如 `--no-gui`） |
| `ASAN_SUPPRESSIONS` | 泄漏抑制清单，**必须存在** |
| `ASAN_ERROR_MARKS` | 内存类指纹，命中即判失败且不豁免 |

**内存指纹优先于 xfail。** 输出里命中 `ASAN_ERROR_MARKS` 里任意一条就判失败，
哪怕该用例在清单里——业务用例可以带病登记（已知的断言失败），内存错误永远不可。
纯业务失败仍按原有语义：`FAIL!` 用例集合全部命中清单才算通过，漏登记一个即判负。

## 已登记问题（xfail）

门禁对历史遗留需要一个中性档位：既不放任整仓全红（那会让人直接无视门禁），
也不逼着一次性豪赌式大改。清单给的就是这一档：

- 命中的违规**不计失败**，但仍回显并标注 `(xfail)`；
- 未登记的新违规照判失败；
- 登记项已修复（本次没命中）则提示可移除；`XFAIL_STRICT = True` 时直接判
  失败，防止清单腐化。

清单缺省放在 `ROOT/ci.xfail`（`XFAIL_FILE` 可改位置；文件不存在等于没有
清单）。纯文本，一行一条，`#` 起注释：

```
# 已登记的历史遗留
[test]
TestMath::testSub()
[memcheck]
NormalButton::
sig:1a2b3c4d
```

分段头可省略：省略时按条目形状推断——`sig:` 开头或 `::` 结尾按 `memcheck`，
其余按 `test`。用例名带 `::`、与符号前缀形状相近，拿不准时用分段头写死。

清单语法错误会带上行号当场报错（`XFAIL_BAD_FILE`），不会变成"门禁莫名其妙
变红"。

风格侧另有一份独立清单，归另一个项目管；本项目不读也不解析它。

## 错误码

错误码是 16 位整数：高 8 位是错误类型，低 8 位是该类型内的具体编号，
定义见 `scripts/common/errors.py`。

| 类型 | 含义 |
| --- | --- |
| 1 | 用法或配置有误 |
| 2 | 缺少外部依赖 |
| 3 | 构建未通过 |
| 4 | 测试未通过 |
| 5 | 内存检查未通过 |
| 8 | 阶段超时 |
| 9 | 被中断 |
| 10 | 阶段在当前机器上不可用（缺外部工具） |

进程退出状态只有 8 位，因此退出时取错误类型（被中断沿用 130），具体编号
由日志给出。跨进程读到的退出状态可用 `compose` 还原成错误码。

几个需要区分的近邻：`XFAIL_BAD_FILE`（清单语法错误，配置问题）与
`XFAIL_STALE`（登记项已修复，清单该清理了）；`STAGE_UNAVAILABLE`
（机器上缺工具，跳过）与 `TOOL_MISSING` 之外的构建失败（代码或构建脚本问题）。

## 测试

```bash
python3 ci/tests/run.py                  # 单元 + 集成 + 冒烟
python3 -m pytest ci/tests/unit          # 单元
python3 -m pytest ci/tests/integration   # 集成
```

测试自身也受 3.4 约束（它们要在老机器上跑），不写 f-string、`pathlib.read_text`、
`subprocess.run` 这类新接口。

## 依赖

**零第三方依赖**：只用标准库，因此老机器上不需要 pip。清单见
[requirements/ci.txt](../requirements/ci.txt)——空清单，用于让"零依赖"成为可被
检查看见的事实。

| 依赖 | 用于 | 说明 |
| --- | --- | --- |
| Python 3.4+ | 全部 | 只用标准库 |
| cmake / qmake / qmake6 | build、build_test | 按 `BUILD_TOOL` 选一个 |
| valgrind | memcheck（valgrind 路线） | 3.8 起可用（`--errors-for-leak-kinds` 是 3.9 才有，未使用） |
| asan / lsan 运行库 | memcheck（asan 路线） | 随编译器提供，路径由 `CXX_COMPILER -print-file-name=libasan.so` 取得；缺了阶段标 `[SKIPPED]` |

工具缺失时阶段不会被判红：`ci.py` 标 `[SKIPPED]` 并给出原因，汇总里单列
"未执行"——**跳过不等于通过**，它只是不把"机器上没装"错报成"代码有问题"。

### 用到的现成接口

| 环节 | 现成接口 | 自己不再做的事 |
| --- | --- | --- |
| 内存归因 | valgrind `--xml` | 不再要求使用者先学会抑制 DSL |
| 内存错误检测 | asan / lsan 拦截器 | 不自己写越界与释放后使用检查，也不靠 ptrace 或慢速模拟 |
| 构建 | cmake / qmake 本体 | 只做参数拼装与输出解析 |
| 并行 | `concurrent.futures` | 不再自建线程池 |

### 有意不采用

| 现成方案 | 为什么不直接用 |
| --- | --- |
| gtest / QTest / Catch2 的 XML / JUnit 报告器 | 格式比文本可信，但要求改项目的 `TEST_COMMAND`（`--gtest_output=`、`-o`、`--reporter`），与"零文件接入"冲突；Qt 5.6 的 QTest 实测也不支持 `-o junitxml`。文本适配器是默认档，项目若已让 runner 输出结构化报告，可用 `regex` 适配器按 `#fail` 语义接进来 |
| gcc `-fdiagnostics-format=json` | 只有 gcc 9+ 支持，clang 不支持；`CXX_COMPILER` 可配任意编译器（含 gcc 4.4），换成它会把兼容面砍掉一半 |
| 手写 valgrind 抑制文件 | 抑制 DSL 的命名块与帧链随编译器/glibc 版本过期；`MEMCHECK_SUPPRESSIONS` 仍保留为可选精修 |
