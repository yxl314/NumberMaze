# stacklog

基于线程局部栈的调用链日志系统。作用域开始一个操作并入栈、析构出栈，操作以父子编号关联成树；
操作可交接给其他线程，接入线程据此把后续操作挂到同一棵树上，记录流离线即可重建完整调用链。
C++11，只用标准库。

## 特性

- **调用链成树**：每条记录自带链编号、操作编号与父引用，不依赖记录顺序即可还原树形。
- **跨线程连通**：取一次令牌交给别的线程，接续后仍在一棵树里；被接续的操作已结束也无妨。
- **零配置可用**：不调用任何初始化接口也能开始操作与记录，默认输出到标准错误。
- **低开销**：热路径不分配、不加锁，开关操作只碰线程局部状态，写记录只写本线程缓冲。
- **无依赖、可移植**：只用标准库；平台专有的 fork 支持集中在一处且可整体关掉。
- **落盘有界**：文件可按大小轮转并保留固定份数，归档失败也不会丢记录。
- **记录文件可版本化**：结构化文件带标识与布局版本，换过布局的旧文件被明确拒绝而非误读。
- **错误可检索**：错误码、可读原因与后端原始输出分开承载，文本与 JSON 都带这些字段。
- **发布版可裁剪**：按编译期阈值剥离低级别消息，结构记录不受影响，离线树依然完整。

## 快速开始

构建与测试：

```bash
cmake -S . -B build
cmake --build build
ctest --test-dir build
```

集成到自己的工程，两种方式。

源码内嵌：

```cmake
add_subdirectory(stacklog)
target_link_libraries(app PRIVATE stacklog::stacklog)
```

安装后使用：

```bash
cmake --install build --prefix <安装位置>
```

```cmake
find_package(stacklog REQUIRED)
target_link_libraries(app PRIVATE stacklog::stacklog)
```

公开头按库名分目录，因此引用一律带 `stacklog/` 前缀：

```cpp
#include <stacklog/stacklog.hpp>          // 伞头
#include <stacklog/tree/tree.hpp>         // 按模块引入
```

最小用例：

```cpp
#include <stacklog/stacklog.hpp>

int compute(int n)
{
	SL_FUNCTION();                          // 操作名取 __func__
	LOG_DEBUG("n=%d", n);                         // 低于阈值时连格式化都不做
	return n * 2;
}

int main()
{
	{
		SL_SPAN("request");                // 栈空，这里开出一条新链
		compute(3);
	}
	stacklog::flush();                            // 可选；不调用也有 atexit 兜底
	return 0;
}
```

输出（默认到标准错误）每条记录一行：

```text
P-4211-9f3a2c17-1 OP-4211-9f3a2c17-1-7 OP-4211-9f3a2c17-1-3 1730000000123456789 1 info enter decode impl.cpp:88 bytes=128
```

字段依次为：链编号、操作编号、父操作编号、本地时间、线程槽、级别、种类、操作名、开操作位置、消息。
时间渲染为不含空格的 `YYYY-MM-DDTHH:MM:SS.mmm`，因此一条记录始终是一行、字段数固定。
需要纳秒原值请读结构化记录文件，文本格式是给人看的。
线程槽从 1 起分配，每个线程一个，同进程内不重复。

## 常用用法

### 级别与输出位置

```cpp
stacklog::set_level(stacklog::Level::debug);       // 默认 info
stacklog::log_to_file("app.log");                  // 默认标准错误；返回是否打开成功
stacklog::set_ring_capacity(4096);                 // 每线程缓冲条数，默认 256
stacklog::set_flush_interval_ms(0);                // 后台排空间隔，默认 100ms；0 表示只手动 flush
```

`LOG_DEBUG` / `LOG_INFO` / `LOG_WARN` / `LOG_ERROR` 先判阈值再格式化，低于阈值不付格式化开销。
格式串按 printf 规则声明，编译器支持时格式串与实参不匹配会直接编译告警。
各项配置互不覆盖、随时可改，后续记录立即生效。

**级别只过滤事件**：作用域的 enter/exit 记录始终产生，否则调用链树会消失、留下的事件还会指向
不存在的操作。所以识别结构要看 `record.kind`，不要看 `record.level`；想少收结构记录，
在 sink 里按 `kind` 丢弃。

### 落盘：轮转、可靠性、刷盘策略

```cpp
stacklog::RotatingOptions options;
options.max_bytes = 5 * 1024 * 1024;    // 单文件上限，默认 5 MB
options.max_files = 5;                  // 保留份数，默认 5
stacklog::log_to_file("app.log", options);

stacklog::log_to_file("app.log");       // 或者用默认轮转参数
stacklog::default_log_path("myapp");    // 默认位置：XDG_STATE_HOME 起三级回退
```

`log_to_file` 会自动逐级创建父目录，并在打开后试探写一次，确认真的写得进去——
只检查"能否打开"会漏掉目录只读、配额用尽这类情况。

轮转失败（目标被占、目录只读）时**不会丢记录**：继续追加现行文件，停止后续轮转尝试，只提示一次。
归档链有缺口也不算失败。

```cpp
stacklog::set_flush_on_level(stacklog::Level::error, true);  // error 记录立即排空
stacklog::set_sync_mode(true);        // 每条记录即排空，行为对齐同步直写，仅排障用
```

默认是 100 毫秒批量排空，因此崩溃时最多丢失最后 100 毫秒与缓冲内的记录。
需要"错误日志必须落盘"时打开第一个开关。

### 错误记录

```cpp
stacklog::log_error(0x0000A1B2u, "无法打开配置文件", "open() failed: No such file");
// 文本：... code=0x0000A1B2 无法打开配置文件\nopen() failed: No such file
```

错误码、可读原因与后端原始输出分开承载：事后可按错误码检索、按原因归类，原始输出保留现场。

运行时参数检查同样记成 error 事件，与断言的区别是发布版恒生效：

```cpp
SL_CHECK_RET(count > 0, -1, "数量必须为正");   // 记 error 事件并返回 -1
SL_CHECK_VOID(ptr != 0, "指针不能为空");       // 记 error 事件并返回
SL_CHECK(a == b, "两者应相等");                // 只记，不影响流程
```

条件原文由宏自动附上，不必手写。

### 终端彩色与双写

```cpp
stacklog::StderrSink err;                                   // 默认自动检测颜色
stacklog::FileSink file("app.log");
stacklog::TeeSink tee(file, err);                           // 文件 + 终端
stacklog::use_sink(tee);
```

颜色仅在标准错误是交互终端、且 `NO_COLOR` 为空时启用，每次写出时现判。
Tee 先写主通道，镜像失败不影响主通道。

### 编译期裁剪

```bash
g++ -DSTACKLOG_MIN_LEVEL=warn ...      # 剥离 debug 与 info 的消息宏
```

被裁掉的消息宏实参不求值、格式串不进入目标文件。
**结构记录不参与裁剪**：`SL_SPAN` / `SL_FUNCTION` 照常产生 enter/exit，离线建出的树依然完整。

### 跨线程

```cpp
void worker(stacklog::CapturedSpan captured)
{
	SL_ATTACH(captured, "worker");             // 以搬运来的操作为父
	LOG_INFO("done");
}

void dispatch()
{
	SL_SPAN("dispatch");
	const stacklog::CapturedSpan captured = stacklog::capture_span();
	std::thread worker_thread(worker, captured);     // 按值传递：跨线程边界自带同步
	worker_thread.join();
}
```

取到的令牌必须经同步边界交给目标线程（任务闭包按值传递、互斥量或原子量中转），不得用无同步的全局变量传令牌。
工作线程不接续别的线程时无需特殊接口：在它自己的栈空处开始操作，得到的就是一条新链的根操作。

### 自定义 sink

继承 `stacklog::Sink`，实现 `write`（消费一条记录）与 `flush`（落盘），再交给 `stacklog::use_sink`。
`write` 只在排空路径上被调用（后台排空线程，或手动 `flush()` 的调用线程），同一时刻只有一个调用者；
实现内部不得再调用本库的接口。

**sink 的生存期**：交给 `use_sink` 之后，sink 析构时库会自动摘掉它并回退到默认 sink，
不会去调用一个已经析构的对象。因此局部 sink 也是安全的，代价只是它析构之后到重新登记之间的记录
会写进默认 sink（标准错误），并给出一行诊断。

真正的顺序陷阱在别处：**晚于库初始化才构造的静态对象，析构会早于库的退出兜底排空**。
若你希望退出前最后一批记录落进自己的 sink，就在任何库调用之前构造它（放命名空间作用域即可），
否则那批记录会走默认 sink。

## 开链时机

进守卫时看本线程的操作栈：**栈为空就分配一条新链，栈非空就继承栈顶的链**。

因此把整段循环包在一个外层操作里时，循环内每轮都继承同一条链——各逻辑单元成为外层操作下的兄弟子树，
链编号不再等价于"一个逻辑单元"。要让每个单元各成一条链，就不要在外层包操作。

## 出错不静默

后台排空起不来、文件写失败、sink 抛异常、传空消息，都不会被无声吞掉：
诊断写标准错误，丢弃类错误计入 `stacklog::dropped_records()`。
`stacklog::flush()` 在 sink 抛出异常时返回 `false`。

## 本地检查

仓库用 `cxx_ci` 跑本地流水线，它作为子模块放在 `cxx_ci/`：

```bash
git clone --recursive <本仓库>            # 连同子模块一起取出
git submodule update --init --recursive   # 已克隆过则用这条补上
```

`cxx_ci` 现在是两个独立项目：`ci/`（基础流程，Python >= 3.4，零第三方依赖）
与 `standardize/`（排版与风格，Python >= 3.11，pip 可装）。两者不共享代码，只
通过文件与命令行协作，约定见 `cxx_ci/CONTRACT.md`。

两份配置各拷一份（各人本地改各自的，不纳入版本管理）：

```bash
cd cxx_ci
cp ci/config/config.py.example ci/config/config.py
cp standardize/config/config.py.example standardize/config/config.py

python3 ci/ci.py                 # 清理、构建、构建测试、跑测试、内存检查
./run_all.sh                     # 现代机器上的一把梭：上面那条 + 排版与风格
```

`standardize` 也可以单独用（装好后 `pipx install ./standardize`）：

```bash
cxx-standardize check            # 只报告不改动
cxx-standardize style --apply    # 写入风格修复（逐文件跑 VERIFY_COMMAND 校验）
cxx-standardize format --apply   # 写入排版
```

两者都支持 `--paths` 限定范围。`style --apply` 改坏编译的会还原并列入
「需人工处理」，因此它总能成功退出且代码始终可编译。

单独用工具也可以，仓库带 `.clang-tidy` 与 `.clang-format`：

```bash
clang-tidy -p build src/logger.cpp        # 静态检查，需要先构建出编译数据库
clang-format -i include/logger.hpp        # 按项目风格排版单个文件
```

静态检查启用分析器（越界、空指针、资源泄漏这类真实缺陷）与 `readability-*`
风格检查，命名、括号等规则按 `.clang-tidy` 执行。

排版不会重排注释：`format.py` 先把注释换成占位符交给 clang-format，排版后按原列号
还原，注释文字与列位逐字不变，宏体的手工对齐因此也能保住。

## 术语

一个执行单元叫**操作**（span），它的身份是 `stacklog::SpanId`。开始一个操作用 `SL_SPAN`，
跨线程交接用 `capture_span` 与 `attach_span` —— 宏与函数同词，没有第二套说法。

## 平台与标准

需要完整支持 C++11 的编译器与标准库：`thread_local`、`std::atomic`、`std::chrono`、`constexpr` 变量。
POSIX 的 fork 支持集中在一个可选编译的适配层里，没有该接口的平台上其余功能不受影响。

`docs/项目设计.md` 记有完整的设计约束、不变量与验证清单。
