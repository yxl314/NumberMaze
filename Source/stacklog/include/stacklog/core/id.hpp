#ifndef INCLUDE_STACKLOG_CORE_ID_HPP
#define INCLUDE_STACKLOG_CORE_ID_HPP

#include <cstdint>
#include <string>

namespace stacklog
{

/// 进程实例身份。pid 便于人读；nonce 每次进程启动随机生成，fork 后由子进程重新生成。
struct ProcessId
{
    std::uint32_t t_pid;
    std::uint32_t t_nonce;
};

/// 链 id：来源进程 + 进程内递增的链序号。
struct ChainId
{
    ProcessId     t_origin;
    std::uint64_t t_seq;
};

/// 操作 id：来源进程 + 线程槽 + 线程内递增的操作序号。
struct SpanId
{
    ProcessId     t_origin;
    std::uint32_t t_slot;
    std::uint64_t t_seq;
};

/// 全零即无效编号；根操作的 parent 为全零。
bool        is_null(const ChainId& v_id);
bool        is_null(const SpanId& v_id);

bool        operator==(const ProcessId& v_lhs, const ProcessId& v_rhs);
bool        operator!=(const ProcessId& v_lhs, const ProcessId& v_rhs);
bool        operator==(const ChainId& v_lhs, const ChainId& v_rhs);
bool        operator!=(const ChainId& v_lhs, const ChainId& v_rhs);
bool        operator==(const SpanId& v_lhs, const SpanId& v_rhs);
bool        operator!=(const SpanId& v_lhs, const SpanId& v_rhs);

/// 展示形式：链 `P-<pid>-<nonce:8hex>-<seq>`，操作 `OP-<pid>-<nonce:8hex>-<slot>-<seq>`。
std::string to_string(const ChainId& v_id);
std::string to_string(const SpanId& v_id);

/// 解析展示形式；成功时写回数值编号并返回 true，格式不符返回 false。
bool        parse_chain_id(const char* v_text, ChainId& v_out);
bool        parse_span_id(const char* v_text, SpanId& v_out);

} // namespace stacklog

#endif // INCLUDE_STACKLOG_CORE_ID_HPP
