# 数字迷宫 (number-maze)

一款基于 **特殊图连接拓扑** 的数字滑块游戏，使用 Qt6 开发，采用分层架构。

把 1 到 15 的数字重新排回顺序、空格归位右下角即可通关。

## 与普通数字华容道的区别

这是本项目的核心特性，也是难度来源：

**普通华容道**使用 4×4 规则网格，只能上下左右推动。玩家凭空间直觉就能
判断"空格周围的格子可以动"。

**数字迷宫**的节点虽然也按 4×4 方阵摆放，但连接关系是项目专门设计的
拓扑，共 22 条边，其中包含多条**对角线连接**：

```
节点 1 (第0行第0列) 连到 节点 6 (第1行第1列)   ← 对角
节点 3 (第0行第2列) 连到 节点 8 (第1行第3列)   ← 对角
```

因此**不能靠位置直觉判断可移动性**，必须观察实际连线。全图的度数分布也
不均匀（4 个节点度为 2，12 个节点度为 3），进一步提高了难度。

据此，界面**强制显示连线与可移动高亮**，不提供关闭选项——这两种视觉
辅助是玩法必需的信息，关掉之后玩家无从判断。

## 架构

严格 MVC 分层，依赖方向单向向下，**View 与 Model 互不相识**：

```
                     MainWindow            （组合根）
                          │
              ┌───────────┴───────────┐
              │  实例化 V / M / C      │
              │  搭建菜单与状态栏       │
              └───────────┬───────────┘
                          │ 注入
              ┌───────────▼───────────┐
              │      Controller       │  C 层：唯一的连接器
              └──┬─────────────────┬──┘
     信号(nodeClicked)          信号(boardChanged 等)
                 │                 │
    ┌────────────▼───┐        ┌────▼────────────┐
    │   BoardView    │        │    GameModel     │
    │   V 层：绘制    │        │  M 层：数据+逻辑  │
    │   只发信号      │        │  调用后端        │
    └────────────────┘        └────┬────────────┘
                                   │ 委托规则
                          ┌────────▼────────┐
                          │      Core       │
                          │  纯 C++，无 Qt   │
                          └─────────────────┘
```

数据流：

1. 用户点击 → View 发出 `nodeClicked(index)` 信号
2. Controller 接到信号，调用 Model 的 `requestMove(index)`
3. Model 委托 `core::GameState` 执行移动，然后广播 `boardChanged()`
4. Controller 接到广播，调用 `model->boardData()` 等取出数据，
   通过 `view->setBoard()` / `setMovable()` 喂给 View
5. View 用新数据重绘

**约束（由代码结构保证，已逐条核验）**：

| 约束 | 核验方式 |
|---|---|
| V 层不持有 M 层指针，也不 include 其头文件 | `grep -c GameModel Source/NumberNaze/MVC/View/*` 结果为 0 |
| M 层不持有 V 层指针，也不 include 其头文件 | `grep -c BoardView Source/NumberNaze/MVC/Model/*` 结果为 0 |
| V 与 M 之间的一切通信都经过 C 层 | Controller 是唯一同时持有两者引用的类 |
| Core 不含任何 Qt 类型 | `number-maze-core` 静态库不链接 Qt 目标 |

View 的绘制数据全部由外部通过槽函数喂入（`setBoard` / `setConnections`
/ `setMovable` / `setSolved`），它自己不主动去任何地方取数据。

### Core 是纯 C++，这一点由构建系统强制

`number-maze-core` 是一个独立静态库，只链接 C++ 标准库，不链接任何
Qt 目标。若有人在 Core 里引入 Qt 类型，编译会直接失败。

已用 `nm` 核验：Core 目标文件的未定义符号中只有 `libstdc++`
（`std::random_device`、`operator new` 等），**无任何 Qt 符号**。

```
NumberMaze/
├── CMakeLists.txt
├── Doxyfile                    文档生成配置
├── README.md
├── config/
│   ├── ci.py                   门禁配置
│   └── standardize.py          风格与排版配置
├── tools/
│   └── build.sh                配置并构建（供风格检查产出编译数据库）
├── cxx_ci/                     门禁与排版工具（随项目一同携带）
└── Source/
    ├── NumberNaze/             本项目代码
    │   ├── main.cpp            程序入口：日志、单实例守卫、主窗口
    │   ├── Core/               游戏核心（纯 C++）
    │   │   ├── Difficulty.h    难度定义
    │   │   ├── Graph.h/.cpp    图拓扑（22 条边，含对角连接）
    │   │   └── GameState.h/.cpp 棋盘状态与规则
    │   ├── MVC/
    │   │   ├── MainWindow.h/.cpp   组合根：实例化 V/M/C，搭建外壳
    │   │   ├── Model/GameModel.h/.cpp       模型：数据、计时、成绩、调用 Core
    │   │   ├── View/BoardView.h/.cpp        视图：绘制与信号
    │   │   └── Controller/Controller.h/.cpp 控制器：连接 V 与 M
    │   └── tests/              Core 自测（不依赖 Qt）
    │       ├── core_test.cpp
    │       └── restart_test.cpp
    ├── singleproc/             外来库：单实例守卫
    └── stacklog/               外来库：调用链日志
```

各层职责边界：

| 层 | 负责 | 不负责 |
|---|---|---|
| Core | 游戏规则：移动合法性、打乱、撤销、胜利判定 | 任何界面、任何 Qt 类型 |
| Model | 持有状态、广播信号、计时、成绩持久化 | 绘制、控件操作、持有 View |
| View | 绘制棋盘、发出点击信号 | 判断能否移动、改状态、持有 Model |
| Controller | 连接 V 与 M、搬运数据、刷新菜单与状态栏 | 游戏规则、绘制 |
| MainWindow | 实例化 V/M/C、搭建菜单与状态栏 | 信号转接、游戏逻辑 |

## 代码文档

全部公开接口按标准 Doxygen 风格撰写注释：`@file` / `@brief` / `@param`
/ `@return` / `@note` / `@par`。接口的 `@param` 与 `@return` 统一写在
头文件，实现文件只保留 `@brief` 与实现说明，避免两处重复。

生成 HTML 文档：

```bash
doxygen Doxyfile          # 输出到 docs/html/
```

`Doxyfile` 已开启 `WARN_IF_UNDOCUMENTED`，因此**生成过程零警告**即可
确认注释的标签拼写、参数名与函数签名完全吻合。当前状态为零警告。

文档内容包含类关系图、调用图（依赖 `dot`），以及中文注释的完整呈现，
输出语言已设为中文。

## 构建

需要 Qt6 (Widgets)、CMake 3.16+、支持 C++17 的编译器。

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build
./build/number-maze
```

## 测试

Core 层带自测，**不依赖 Qt，可单独编译运行**：

```bash
# 通过 CMake
cd build && ctest --output-on-failure

# 或直接编译，验证 Core 确实是纯 C++（在源码目录下执行）
cd Source/NumberNaze
g++ -std=c++17 -ICore tests/core_test.cpp Core/Graph.cpp Core/GameState.cpp \
    -o core_test && ./core_test
```

覆盖内容：图结构（边数、度数分布、对角连接）、边界条件（空格自身与
非相邻节点不可移动）、移动与撤销、四档难度打乱、可解性验证。

界面部分的验证用虚拟显示，无需真实桌面。构建出可执行文件后直接运行：

```bash
QT_QPA_PLATFORM=offscreen ./build/number-maze
```

## 外来库

`Source/` 下的两个库以子目录方式嵌入构建，只取库本体，
其自测与示例一律关闭，不混入本项目的测试集合：

| 库 | 作用 | 链接目标 |
|---|---|---|
| singleproc | 同一用户下只允许一个实例运行，重复启动时把已有窗口带到前台 | `singleproc::singleproc` |
| stacklog | 基于线程局部栈的调用链日志 | `stacklog::stacklog` |

二者各自声明 C++11，本项目用 C++17。标准由各自的 `CMakeLists.txt`
约束到自己的 target 上，不依赖全局 `CMAKE_CXX_STANDARD`，因此互不影响。

它们被排除在门禁范围之外，也列入 `FROZEN` 不许就地改写——
否则升级库版本时会与上游产生无谓差异。

启动流程（见 `main.cpp`）：先初始化日志，再取单实例守卫。
拿不到独占权说明已有实例在运行，此时旧窗口已被带到前台，本次进程直接退出。
日志先于守卫初始化，"已有实例"这类正常结局也能留下记录。

日志默认落在 `$XDG_STATE_HOME/number-maze/number-maze.log`
（未设该变量时为 `~/.local/state/number-maze/number-maze.log`）。

## 门禁与风格

项目自带 `cxx_ci`，一条命令跑完构建、测试、内存检查与风格排版：

```bash
./cxx_ci/run_all.sh
```

配置在 `config/` 下，两个入口各读自己那份：

| 文件 | 作用 |
|---|---|
| `config/ci.py` | 构建、测试、内存检查的范围与指令 |
| `config/standardize.py` | 排版、风格、命名口径 |

只跑基础流程（不含会改写工作区的风格部分）：

```bash
python3 cxx_ci/ci/ci.py --config config/ci.py
```

`ci.py` 的门禁阶段不具改写能力，风格与排版由 `cxx-standardize` 负责，
`--apply` 才会真正改文件。查看当前生效的配置：

```bash
python3 cxx_ci/ci/ci.py doctor --config config/ci.py
cxx-standardize doctor --config config/standardize.py
```

## 玩法

- 点击**与空格相连**的数字，它会滑入空格
- 绿色高亮的节点表示当前可移动，虚线圆圈表示空格
- 把所有数字恢复成 `1..15` 顺序（空格在右下角）即通关

| 操作 | 方式 |
|---|---|
| 移动数字 | 鼠标点击 |
| 悔棋 | 工具栏按钮 / `Ctrl+Z` |
| 重开本局 | 工具栏按钮 / `Ctrl+R` |
| 更换难度 | 菜单「游戏」→「新游戏」 |

### 难度

| 难度 | 打乱步数 |
|---|---|
| 简单 | 20 |
| 普通 | 60 |
| 困难 | 150 |
| 专家 | 300 |

## 关于可解性

**任何局面都保证可解**，这是构造性保证，而非概率：

打乱不是随机排列，而是从已解状态出发执行**若干次合法移动**。因此沿着
这串移动的反方向走回去，必然回到已解状态。用一个简单的归纳就能证明：
每一步合法移动都可逆，所以整条路径可逆。

这也意味着本项目**不需要**标准华容道那套"逆序数 + 空格行号"的奇偶性
判据——那套判据只在"随机排列初始状态"时才需要。

另外，打乱不计入撤销历史，避免玩家用悔棋一路退回已解状态。
