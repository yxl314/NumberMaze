#include <stacklog/core/id.hpp>

namespace stacklog
{
namespace
{

void append_hex8(std::string& v_out, std::uint32_t v_value)
{
    static const char s_digits[] = "0123456789abcdef";
    for (int shift = 28; shift >= 0; shift -= 4)
    {
        v_out.push_back(s_digits[(v_value >> shift) & 0xFu]);
    }
}

bool is_hex_digit(char v_c)
{
    return (v_c >= '0' && v_c <= '9') || (v_c >= 'a' && v_c <= 'f');
}

/// 游标式解析：从 text 的 *pos 处读固定宽度的小写十六进制。
bool read_hex8(const char* v_text, std::size_t& v_pos, std::uint32_t& v_out)
{
    std::uint32_t value = 0;
    for (int i = 0; i < 8; ++i)
    {
        const char c = v_text[v_pos + i];
        if (!is_hex_digit(c))
        {
            return false;
        }
        value = (value << 4) | static_cast<std::uint32_t>(c <= '9' ? c - '0' : c - 'a' + 10);
    }
    v_pos += 8;
    v_out = value;
    return true;
}

bool read_decimal(const char* v_text, std::size_t& v_pos, std::uint64_t& v_out)
{
    if (v_text[v_pos] < '0' || v_text[v_pos] > '9')
    {
        return false;
    }
    std::uint64_t value = 0;
    while (v_text[v_pos] >= '0' && v_text[v_pos] <= '9')
    {
        value = value * 10 + static_cast<std::uint64_t>(v_text[v_pos] - '0');
        ++v_pos;
    }
    v_out = value;
    return true;
}

bool expect(const char* v_text, std::size_t& v_pos, char v_expected)
{
    if (v_text[v_pos] != v_expected)
    {
        return false;
    }
    ++v_pos;
    return true;
}

bool read_process(const char* v_text, std::size_t& v_pos, ProcessId& v_out)
{
    std::uint64_t pid   = 0;
    std::uint32_t nonce = 0;
    if (!read_decimal(v_text, v_pos, pid))
    {
        return false;
    }
    if (!expect(v_text, v_pos, '-'))
    {
        return false;
    }
    if (!read_hex8(v_text, v_pos, nonce))
    {
        return false;
    }
    if (pid > 0xFFFFFFFFull)
    {
        return false;
    }
    v_out.t_pid   = static_cast<std::uint32_t>(pid);
    v_out.t_nonce = nonce;
    return true;
}

/// 来源进程是否为全零。
bool origin_is_null(const ProcessId& v_id)
{
    return v_id.t_pid == 0 && v_id.t_nonce == 0;
}

} // namespace

bool is_null(const ChainId& v_id)
{
    return origin_is_null(v_id.t_origin) && v_id.t_seq == 0;
}

bool is_null(const SpanId& v_id)
{
    return origin_is_null(v_id.t_origin) && v_id.t_slot == 0 && v_id.t_seq == 0;
}

bool operator==(const ProcessId& v_lhs, const ProcessId& v_rhs)
{
    return v_lhs.t_pid == v_rhs.t_pid && v_lhs.t_nonce == v_rhs.t_nonce;
}

bool operator!=(const ProcessId& v_lhs, const ProcessId& v_rhs)
{
    return !(v_lhs == v_rhs);
}

bool operator==(const ChainId& v_lhs, const ChainId& v_rhs)
{
    return v_lhs.t_origin == v_rhs.t_origin && v_lhs.t_seq == v_rhs.t_seq;
}

bool operator!=(const ChainId& v_lhs, const ChainId& v_rhs)
{
    return !(v_lhs == v_rhs);
}

bool operator==(const SpanId& v_lhs, const SpanId& v_rhs)
{
    return v_lhs.t_origin == v_rhs.t_origin && v_lhs.t_slot == v_rhs.t_slot && v_lhs.t_seq == v_rhs.t_seq;
}

bool operator!=(const SpanId& v_lhs, const SpanId& v_rhs)
{
    return !(v_lhs == v_rhs);
}

std::string to_string(const ChainId& v_id)
{
    std::string out;
    out.reserve(32);
    out += "P-";
    out += std::to_string(v_id.t_origin.t_pid);
    out.push_back('-');
    append_hex8(out, v_id.t_origin.t_nonce);
    out.push_back('-');
    out += std::to_string(v_id.t_seq);
    return out;
}

std::string to_string(const SpanId& v_id)
{
    std::string out;
    out.reserve(40);
    out += "OP-";
    out += std::to_string(v_id.t_origin.t_pid);
    out.push_back('-');
    append_hex8(out, v_id.t_origin.t_nonce);
    out.push_back('-');
    out += std::to_string(v_id.t_slot);
    out.push_back('-');
    out += std::to_string(v_id.t_seq);
    return out;
}

bool parse_chain_id(const char* v_text, ChainId& v_out)
{
    if (v_text == 0)
    {
        return false;
    }
    std::size_t pos = 0;
    if (!expect(v_text, pos, 'P') || !expect(v_text, pos, '-'))
    {
        return false;
    }
    ChainId parsed;
    if (!read_process(v_text, pos, parsed.t_origin))
    {
        return false;
    }
    if (!expect(v_text, pos, '-'))
    {
        return false;
    }
    if (!read_decimal(v_text, pos, parsed.t_seq))
    {
        return false;
    }
    if (v_text[pos] != '\0')
    {
        return false;
    }
    v_out = parsed;
    return true;
}

bool parse_span_id(const char* v_text, SpanId& v_out)
{
    if (v_text == 0)
    {
        return false;
    }
    std::size_t pos = 0;
    if (!expect(v_text, pos, 'O') || !expect(v_text, pos, 'P') || !expect(v_text, pos, '-'))
    {
        return false;
    }
    SpanId parsed;
    if (!read_process(v_text, pos, parsed.t_origin))
    {
        return false;
    }
    if (!expect(v_text, pos, '-'))
    {
        return false;
    }
    std::uint64_t slot = 0;
    if (!read_decimal(v_text, pos, slot) || slot > 0xFFFFFFFFull)
    {
        return false;
    }
    parsed.t_slot = static_cast<std::uint32_t>(slot);
    if (!expect(v_text, pos, '-'))
    {
        return false;
    }
    if (!read_decimal(v_text, pos, parsed.t_seq))
    {
        return false;
    }
    if (v_text[pos] != '\0')
    {
        return false;
    }
    v_out = parsed;
    return true;
}

} // namespace stacklog
