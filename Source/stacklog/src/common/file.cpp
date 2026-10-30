#include <stacklog/common/file.hpp>

#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>

#if defined(__unix__) || defined(__APPLE__)
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>
#endif

namespace stacklog
{
namespace common
{

namespace
{

/// 取环境变量，空串按未设置处理。
const char* non_empty_env(const char* v_name)
{
    const char* value = std::getenv(v_name);
    return (value != 0 && value[0] != '\0') ? value : 0;
}

/// 是否为绝对路径。用'/'判定，Windows 盘符形式不在此列。
bool is_absolute(const char* v_path)
{
    return v_path != 0 && v_path[0] == '/';
}

} // namespace

bool make_directories(const char* v_path)
{
#if defined(__unix__) || defined(__APPLE__)
    if (v_path == 0 || v_path[0] == '\0')
    {
        return false;
    }

    std::string       current;
    const std::string target(v_path);
    for (std::size_t i = 0; i <= target.size(); ++i)
    {
        if (i != target.size() && target[i] != '/')
        {
            continue;
        }
        if (i == 0)
        {
            current = "/";
            continue;
        }
        current.assign(target, 0, i);
        if (current.empty() || current == "/")
        {
            continue;
        }
        if (::mkdir(current.c_str(), 0700) != 0 && errno != EEXIST)
        {
            return false;
        }
    }
    return true;
#else
    (void)path;
    return false; // 该平台没有此接口，如实返回失败
#endif
}

std::string parent_of(const std::string& v_path)
{
    const std::size_t slash = v_path.find_last_of('/');
    if (slash == std::string::npos)
    {
        return std::string();
    }
    if (slash == 0)
    {
        return std::string("/");
    }
    return v_path.substr(0, slash);
}

bool is_writable_directory(const char* v_path)
{
    if (v_path == 0 || v_path[0] == '\0')
    {
        return false;
    }
#if defined(__unix__) || defined(__APPLE__)
    // 用 unique 后缀避免与真实文件重名；探测完即删。
    char      probe[4096];
    const int written =
        std::snprintf(probe, sizeof(probe), "%s/.stacklog-write-probe-%ld", v_path, static_cast<long>(::getpid()));
    if (written <= 0 || static_cast<std::size_t>(written) >= sizeof(probe))
    {
        return false;
    }
    std::FILE* file = std::fopen(probe, "wb");
    if (file == 0)
    {
        return false;
    }
    std::fclose(file);
    ::unlink(probe);
    return true;
#else
    (void)path;
    return false;
#endif
}

std::string default_state_directory()
{
    const char* state = non_empty_env("XDG_STATE_HOME");
    if (state != 0 && is_absolute(state))
    {
        return std::string(state);
    }

    const char* home = non_empty_env("HOME");
    if (home != 0 && is_absolute(home))
    {
        return std::string(home) + "/.local/state";
    }

    return std::string(".");
}

std::string default_log_path(const char* v_application)
{
    const std::string name = (v_application != 0 && v_application[0] != '\0') ? v_application : "app";
    return default_state_directory() + "/" + name + "/" + name + ".log";
}

} // namespace common
} // namespace stacklog
