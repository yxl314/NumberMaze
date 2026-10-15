#include <stacklog/common/diagnose.hpp>

#include <cerrno>
#include <cstdio>
#include <cstring>

namespace stacklog
{
namespace common
{

void diagnose(const char* v_message)
{
    std::fputs("stacklog: ", stderr);
    std::fputs(v_message == 0 ? "" : v_message, stderr);
    std::fputc('\n', stderr);
    std::fflush(stderr);
}

void diagnose_errno(const char* v_message)
{
    std::fputs("stacklog: ", stderr);
    std::fputs(v_message == 0 ? "" : v_message, stderr);
    const int error = errno;
    if (error != 0)
    {
        std::fputs(": ", stderr);
        std::fputs(std::strerror(error), stderr);
    }
    std::fputc('\n', stderr);
    std::fflush(stderr);
}

} // namespace common
} // namespace stacklog
