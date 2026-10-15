#ifndef INCLUDE_SINGLEPROC_CALLBACK_HPP
#define INCLUDE_SINGLEPROC_CALLBACK_HPP

#include <functional>
#include <string>

namespace singleproc
{

/// 主实例侧：收到次实例的请求时调用，返回值原样作为应答回给对方。
///
/// 使用者在这里决定"来了一个新实例"意味着什么：拒绝、上报状态、要求对方退出，
/// 都由回调自己实现。库不规定内容格式，也不解释返回值。
typedef std::function<std::string(const std::string& v_request)> OnRequest;

/// 次实例侧：拿到主实例的应答后调用，返回值表示是否继续运行。
/// 返回假表示调用方打算就此退出；库不代为退出，只把这个结果交给使用者判断。
typedef std::function<bool(const std::string& v_response)>       OnResponse;

} // namespace singleproc

#endif // INCLUDE_SINGLEPROC_CALLBACK_HPP
