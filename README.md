# 数字迷宫 (number-maze) —— Qt 5 版

一款基于**特殊图连接拓扑**的数字滑块游戏。把 1 到 15 的数字重新排回顺序、
空格归位右下角即可通关。

本目录是原 Qt 6 项目的 **Qt 5 移植版**，保留同一套 Core 与 MVC 分层，
改用 qmake 构建，并把运行库随发行包一起携带，目标是在
**Ubuntu 16.04 ~ 26.04** 上都能直接运行。

## 与普通数字华容道的区别

普通华容道使用 4×4 规则网格，只能上下左右推动，玩家凭空间直觉就能判断
“空格周围的格子可以动”。

数字迷宫的节点虽然也按 4×4 方阵摆放，但连接关系是专门设计的拓扑：共
22 条边，其中包含多条**对角线连接**（例如 1–6、3–8）。因此不能靠位置
直觉判断可移动性，必须观察实际连线。全图度数分布也不均匀（4 个节点度为
2，12 个节点度为 3），进一步提高了难度。

界面**强制显示连线与可移动高亮**，不提供关闭选项——这两种视觉辅助是
玩法必需的信息。

## 玩法

- 点击与空格相连的数字，它会滑入空格
- 绿色高亮的节点表示当前可移动，虚线圆圈表示空格
- 把所有数字恢复成 `1..15` 顺序（空格在右下角）即通关

| 操作 | 方式 |
|---|---|
| 移动数字 | 鼠标点击 |
| 悔棋 | 工具栏按钮 / `Ctrl+Z` |
| 重开本局 | 工具栏按钮 / `Ctrl+R` |
| 更换难度 | 菜单「游戏」→「新游戏」 |

| 难度 | 打乱步数 |
|---|---|
| 简单 | 20 |
| 普通 | 40 |
| 困难 | 80 |
| 专家 | 160 |

打乱是从已解状态出发执行若干次**合法移动**，因此任何开局都必然可解；
打乱不计入撤销历史，避免用悔棋一路退回已解状态。

## 架构

严格 MVC 分层，依赖方向单向向下，View 与 Model 互不相识：

```
MainWindow（组合根）
   ├── BoardView（V：只绘制、只发信号）
   ├── GameModel（M：状态、计时、成绩，调用 Core）
   └── Controller（C：唯一的连接器）

core::GameState + core::Graph（纯 C++，无 Qt）
```

数据流：用户点击 → View 发出 `nodeClicked` → Controller 调用
Model 的 `requestMove` → Model 委托 Core 执行并广播 `boardChanged` →
Controller 取出局面数据喂给 View → View 重绘。

Core 是纯 C++：`Graph`、`GameState`、`Difficulty` 不包含任何 Qt 头文件，
可脱离 Qt 单独编译和测试。

## 构建

需要 Qt 5（Widgets）、qmake、支持 C++14 的编译器。Ubuntu 16.04 上：

```bash
sudo apt-get install -y qtbase5-dev qt5-qmake g++ make
qmake number-maze.pro
make -j$(nproc)
./build/number-maze
```

Qt 5.5 的 qmake 不支持 `CONFIG += c++14`，项目在 `.pro` 中直接指定
`-std=c++14`，因此 g++ 5.4 也能编译。

## 测试

Core 自测（不依赖 Qt）：

```bash
cd Source/NumberNaze
g++ -std=c++14 -Wall -Wextra -ICore tests/core_test.cpp \
    Core/Graph.cpp Core/GameState.cpp -o /tmp/core-test && /tmp/core-test
```

模型自测（覆盖“重开本局”的快照语义）：

```bash
cd Source/NumberNaze/tests
qmake model-test.pro && make -j$(nproc)
./build-model/model-test
```

## 打包与兼容性

在 Ubuntu 16.04（Qt 5.5.1 / glibc 2.23）上构建并打包：

```bash
qmake number-maze.pro && make -j$(nproc)
packaging/make-run.sh 1.0.0
# 产物：dist/NumberMaze-1.0.0-qt5-x86_64.run
```

安装包是自解压 shell 脚本，默认装到 `~/.local`，不需要 root，也不需要
目标机安装 Qt：

```bash
./NumberMaze-1.0.0-qt5-x86_64.run
~/.local/bin/number-maze

./NumberMaze-1.0.0-qt5-x86_64.run --prefix /opt/number-maze
./NumberMaze-1.0.0-qt5-x86_64.run --uninstall
```

包内自带：

- Qt 5.5 的 Core / Gui / Widgets / DBus / XcbQpa
- xcb 平台插件（含 offscreen，便于无显示自检）
- 新版 Ubuntu 已移除的 `libicu*.so.55`、`libpng12`、`libpcre16`、`libpcre`
- xcb 扩展库
- 界面所需的 Noto Sans CJK SC 子集字体（约 300 KB）

**为什么在 16.04 上构建**：二进制以 glibc 2.23 为基线，较新的 Ubuntu
可以运行旧二进制，反之不行。Qt 运行库随包携带，系统侧只依赖 X11、GL、
fontconfig、freetype、xkbcommon 等各代 Ubuntu 都有的基础库。

已在以下环境实际验证：

| 环境 | 方式 | 结果 |
|---|---|---|
| Ubuntu 16.04.7（Qt 5.5.1） | 源码构建 + 安装包 xcb 运行 | 通过 |
| Ubuntu 26.04（XWayland / X11） | 安装包 xcb 运行 | 通过 |
| Ubuntu 16.04 / 26.04 | 安装包 offscreen 启动 | 通过 |

## Qt 5 适配要点

- `<QAction>` 在 Qt 5 属于 QtWidgets；系统若同时装了 Qt3D，可能有同名头
  文件抢先命中，因此显式写 `#include <QtWidgets/QAction>`。
- Qt 5.5 没有 `QFontMetrics::horizontalAdvance`，用 `QT_VERSION` 分支退回
  `width()`。
- 固定使用 Fusion 风格，不依赖各发行版的 gtk 主题插件。
- 内置一份中文字体子集，目标机缺少 CJK 字体时也不会出现方框。
- 修复了原模型中“重开本局”快照从未赋值的问题，重开现在会真正回到本局
  开局局面（新增 `tests/model_test.cpp` 覆盖）。

## 单实例与日志

`main.cpp` 使用嵌入式 singleproc 库保证同一用户只运行一个实例；重复启动
时把已有窗口带到前台。日志由 stacklog 写入
`$XDG_STATE_HOME/number-maze/number-maze.log`
（未设该变量时为 `~/.local/state/number-maze/number-maze.log`）。

## 目录

```
number-maze.pro             qmake 工程
Source/NumberNaze/
  main.cpp                  入口：字体、风格、日志、单实例、主窗口
  Core/                     纯 C++：图拓扑、局面与规则
  MVC/                      MainWindow / Model / View / Controller
  tests/                    core_test.cpp、model_test.cpp 与 .pro
Source/singleproc/          外来库：单实例守卫
Source/stacklog/            外来库：调用链日志
resources/                  内置字体子集、图标与 qrc
packaging/                  自解压安装包脚本与桌面项模板
```
