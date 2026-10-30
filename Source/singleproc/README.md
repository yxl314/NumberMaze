# singleproc

让同一个程序在同一台机器上同一时刻只运行一个实例。

新实例启动时若发现已有实例，双方可以交换一条消息；交换之后各自做什么，
完全由使用者决定——库不规定"该拒绝还是该接管"，也不解释消息内容。

## 特性

- **异常终止不留残留**：互斥由文件锁承担，进程终止时由内核释放，
  强杀之后下一次启动不受影响。
- **交互由使用者定义**：只提供一条请求与一条应答的通道，内容与含义自定。
- **不做多余的事**：不自建线程、不注册信号处理器、不解析消息、不接管进程退出。
- **出错不静默**：每一种失败都有对应的结果码与说明。

## 快速开始

```bash
cmake -S . -B build
cmake --build build
ctest --test-dir build
```

集成到自己的工程，两种方式。

源码内嵌：

```cmake
add_subdirectory(singleproc)
target_link_libraries(app PRIVATE singleproc::singleproc)
```

安装后使用：

```bash
cmake --install build --prefix <安装位置>
```

```cmake
find_package(singleproc REQUIRED)
target_link_libraries(app PRIVATE singleproc::singleproc)
```

公开头按库名分目录，引用一律带 `singleproc/` 前缀：

```cpp
#include <singleproc/singleproc.hpp>      // 伞头
#include <singleproc/guard.hpp>           // 按需引入
```

最小用例：

```cpp
#include <singleproc/singleproc.hpp>

int main()
{
    singleproc::Options options;
    options.name = "my-daemon";
    options.request = "status";        // 发现已有实例时发这条过去

    singleproc::Guard guard(
        options,
        // 主实例：来了新实例时怎么办，由这里决定
        [](const std::string& request) -> std::string {
            return request == "status" ? "running" : "busy";
        },
        // 次实例：拿到应答后决定自己还起不起
        [](const std::string& response) -> bool {
            return response != "running";
        });

    if (!guard.is_primary())
        return 0;                      // 已有实例在跑，本实例不启动

    for (;;)
        guard.serve_once();            // 阻塞到有连接到来
    return 0;
}
```

## 三种启动结局

| 状态 | 含义 |
|---|---|
| `primary` | 取得独占权，成为主实例 |
| `owned_by_other` | 已有实例在运行，没有做交换（不需要，或对方没在服务） |
| `secondary` | 已有实例在运行，且完成了一次请求应答交换，应答在 `response()` |

`secondary` 与 `owned_by_other` 分开，是因为"锁被别人拿着"与"和对方说上话了"是两件事：
混成一个状态，调用方就无法判断该不该重试或降级。

**只想要互斥、不需要交互**时，不给 `Options::request` 也不给应答回调，
启动就不会去连接、也不会白等一个超时。

## 通信

一问一答。`Options::request` 发过去，主实例 `OnRequest` 的返回值发回来。
消息按 4 字节大端长度前缀成帧，读写都循环到读满为止；两端都设超时；
长度上限取自 `Options::max_message_bytes`，超出即报错，不截断。

`serve_once()` 会阻塞到有连接到来，所以可以直接放在循环里，不用空转。
它只在主实例上有意义，次实例调用返回 `not_primary`。

## 路径

| 项 | 取值 |
|---|---|
| 目录 | `Options::directory`，为空时用 `XDG_RUNTIME_DIR`，再退到 `/tmp` |
| 锁文件 | `<目录>/<实例名>.lock` |
| 套接字 | `<目录>/<实例名>.sock` |

套接字与锁文件权限都是 `0600`。派生路径超过平台上限时返回 `path_too_long`，
不截断、不静默换名。

`XDG_RUNTIME_DIR` 不可用时会退到 `/tmp`。这在多用户机器上不是期望的位置，
所以实际目录与是否回退都能查出来：`guard.directory()` 与
`guard.used_fallback_directory()`。

## 边界

- **只有建立过监听的实例才删除套接字文件**，次实例不会动它。
- 主实例崩溃后套接字文件会残留；新实例能辨认出"没有人在监听"并清理它。
- 主实例不调用 `serve_once()` 时，次实例在超时后得到 `peer_timeout`。
- `fork` 之后子进程继承了描述符，不应再认为自己独占；这由使用者在 `fork` 后自行处理。
- 静态守卫的析构顺序：守卫应在任何可能使用它的对象之前构造。

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

`style --apply` 改坏编译的会还原并列入「需人工处理」，因此它总能成功退出且代码
始终可编译。排版不会重排注释——注释在排版后按原列号还原，注释文字与列位不变。
仓库带 `.clang-tidy` 与 `.clang-format`，也可以单独使用这两个工具：

```bash
clang-tidy -p build src/guard.cpp         # 静态检查，需要先构建出编译数据库
clang-format -i include/singleproc/guard.hpp
```

## 待扩展

多用户隔离与权限模型、非 POSIX 平台、跨命名空间与容器、长连接与主动推送、
消息编解码工具、同机多实例共存、主实例转换、用 `fcntl` 记录锁替换 `flock`。

`docs/设计方案.md` 记有完整的设计约束、不变量与验证清单。
