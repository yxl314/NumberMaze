#ifndef INCLUDE_SINGLEPROC_GUARD_HPP
#define INCLUDE_SINGLEPROC_GUARD_HPP

#include <string>

#include <singleproc/callback.hpp>
#include <singleproc/options.hpp>

namespace singleproc
{

/// 本次启动的结局。取值与说明一一对应，调用方据此判断，不靠猜。
enum class Status
{
    Primary,        // 取得独占权，成为主实例
    Secondary,      // 已有实例在运行，且已完成一次请求应答交换
    OwnedByOther, // 已有实例在运行，但未做交换（不需要，或对方没在服务）
    FailedLock,    // 锁文件打不开或加锁失败
    FailedSocket,  // 套接字建立失败（含目录不可写）
    FailedConnect, // 探测已有实例时连接失败，且不属于"死残留"
    PeerTimeout,   // 已有实例未在约定时间内应答
    MessageTooLarge, // 单条消息超过上限
    NotPrimary,    // 非主实例调用了只有主实例才能做的事
    PathTooLong,  // 派生出的路径超过平台限制
};

/// 结果码的名字，用于日志与诊断。
const char* status_name(Status v_status);

/// 独占守卫。
///
/// 构造时尝试取得独占权：成功则成为主实例并建立监听；发现已有实例则尝试与它
/// 通信，把请求交给 `OnRequest` 一方处理、把应答交给 `OnResponse`。
/// 无论哪种结局，对象都构造成功，身份用 `status()` 查询——发现已有实例是正常
/// 局面而非错误，不该逼调用方用异常来表达。
///
/// 析构时关闭监听并释放锁。进程异常终止时锁由内核释放，因此不会留下阻止
/// 后续启动的状态。
///
/// 不可拷贝、不可移动：副本会让"谁负责释放"变得含糊。
class Guard
{
public:
    /// 构造即尝试独占。
    ///
    /// `on_request` 只在主实例上被调用；`on_response` 只在发现已有实例时被调用。
    /// 两者都可为空，为空时使用空请求与"不反对继续"的默认结果。
    Guard(const Options& v_options, const OnRequest& v_on_request, const OnResponse& v_on_response);
    ~Guard();

    /// 本次启动的结局。`primary` 之外都表示没有独占权。
    Status             status() const;

    /// 是否取得独占权。
    bool               is_primary() const;

    /// 发给已有实例的请求。只有发现已有实例时有内容。
    const std::string& request() const;

    /// 已有实例回的应答。只有发现已有实例时有内容。
    const std::string& response() const;

    /// 失败或需要说明时的文字，指向静态存储。
    const char*        message() const;

    /// 实际存放锁文件与套接字的目录：调用方没指定时由环境推导，
    /// 因此要能查出到底用了哪一个。
    const std::string& directory() const;

    /// 是否退到了共享目录（未指定目录且 `XDG_RUNTIME_DIR` 不可用）。
    /// 为真时锁与套接字落在像 /tmp 这样多用户共享的位置，需要留心。
    bool               used_fallback_directory() const;

    /// 处理**一条**连接：接受、调用处理函数、回写应答。
    ///
    /// 会阻塞到有连接到来为止，因此可以直接写成 `while (...) guard.serve_once();`
    /// 而不空转。主实例在自己的循环或线程里调用它，库不自建线程；这样监听何时
    /// 开始、与主循环如何协调都由使用者掌握。
    ///
    /// 非主实例调用返回 `not_primary`，不做任何读写。
    /// 等待期间若监听被关闭（守卫析构）则返回 `not_primary`。
    Status             serve_once();

private:
    Guard(const Guard&)            = delete;
    Guard& operator=(const Guard&) = delete;

    struct Impl;
    Impl* m_impl;
};

} // namespace singleproc

#endif // INCLUDE_SINGLEPROC_GUARD_HPP
