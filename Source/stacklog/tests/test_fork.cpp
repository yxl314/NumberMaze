#include <sys/wait.h>
#include <unistd.h>

#include <atomic>
#include <chrono>
#include <cstdio>
#include <set>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#include "test_util.hpp"

namespace
{

std::string read_file(const char* v_path)
{
    std::FILE* file = std::fopen(v_path, "rb");
    if (file == 0)
    {
        return std::string();
    }
    std::string content;
    char        buffer[512];
    std::size_t read = 0;
    while ((read = std::fread(buffer, 1, sizeof(buffer), file)) > 0)
    {
        content.append(buffer, read);
    }
    std::fclose(file);
    return content;
}

/// 收集文本里每条记录首字段（链 id）的来源进程，键为 "t_pid/t_nonce"。
std::set<std::string> collect_origins(const std::string& v_content)
{
    std::set<std::string> origins;
    std::istringstream    stream(v_content);
    std::string           line;
    while (std::getline(stream, line))
    {
        const std::size_t space = line.find(' ');
        if (space == std::string::npos)
        {
            continue;
        }
        stacklog::ChainId chain;
        if (!stacklog::parse_chain_id(line.substr(0, space).c_str(), chain))
        {
            continue;
        }
        std::ostringstream key;
        key << chain.t_origin.t_pid << '/' << chain.t_origin.t_nonce;
        origins.insert(key.str());
    }
    return origins;
}

std::atomic<bool>      g_stop(false);

testing::RecordingSink g_parent_sink;

void                   noise_worker()
{
    while (!g_stop.load())
    {
        SL_SPAN("noise");
        LOG_INFO("noise");
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
}

void test_fork_keeps_ids_distinct()
{
    const char* parent_path = "stacklog_fork_parent.txt";
    const char* child_path  = "stacklog_fork_child.txt";
    std::remove(parent_path);
    std::remove(child_path);

    stacklog::set_level(stacklog::Level::Info);
    stacklog::log_to_file(parent_path);

    // fork 时让排空线程处于工作状态：更容易撞上"锁被其他线程持有"的情形。
    std::thread noise(noise_worker);

    {
        SL_SPAN("parent-before-fork");
        LOG_INFO("parent work");
    }
    stacklog::flush();

    const pid_t child = ::fork();
    if (child == 0)
    {
        // 子进程：接着记录，写到自己的文件。
        stacklog::log_to_file(child_path);
        {
            SL_SPAN("child-after-fork");
            LOG_INFO("child work");
        }
        stacklog::flush();
        ::_exit(0);
    }

    CHECK(child > 0);
    int status = 0;
    CHECK_EQ(::waitpid(child, &status, 0), child);
    CHECK(WIFEXITED(status));
    CHECK_EQ(WEXITSTATUS(status), 0);

    g_stop.store(true);
    noise.join();

    // 父进程 fork 之后仍能继续记录。
    {
        SL_SPAN("parent-after-fork");
        LOG_INFO("parent work");
    }
    stacklog::flush();

    const std::string parent_content = read_file(parent_path);
    const std::string child_content  = read_file(child_path);
    CHECK(parent_content.find("parent-after-fork") != std::string::npos);
    CHECK(child_content.find("child-after-fork") != std::string::npos);

    const std::set<std::string> parent_origins = collect_origins(parent_content);
    const std::set<std::string> child_origins  = collect_origins(child_content);
    CHECK(!parent_origins.empty());
    CHECK(!child_origins.empty());

    std::size_t shared = 0;
    for (std::set<std::string>::const_iterator it = child_origins.begin(); it != child_origins.end(); ++it)
    {
        if (parent_origins.count(*it) > 0)
        {
            ++shared;
        }
    }
    CHECK_EQ(shared, 0u);   // 子进程换了 nonce，操作 id 不会与父进程相撞
}

void test_fork_inherits_span_stack()
{
    // 继承下来的操作栈保留：子进程在父进程的操作之下继续记录，且不冲突。
    const char* child_path = "stacklog_fork_child_stack.txt";
    std::remove(child_path);

    stacklog::set_level(stacklog::Level::Info);
    stacklog::use_sink(g_parent_sink);

    SL_SPAN("inherited");
    const stacklog::CapturedSpan carried = stacklog::capture_span();
    const pid_t                  child   = ::fork();
    if (child == 0)
    {
        stacklog::log_to_file(child_path);
        stacklog::ScopedSpan attached = stacklog::attach_span(carried, "child-inherited", SL_HERE);
        LOG_INFO("child work");
        stacklog::flush();
        ::_exit(0);
    }

    int status = 0;
    CHECK_EQ(::waitpid(child, &status, 0), child);
    CHECK(WIFEXITED(status));
    CHECK_EQ(WEXITSTATUS(status), 0);
    CHECK(read_file(child_path).find("child-inherited") != std::string::npos);
}

} // namespace

int main()
{
    RUN(test_fork_keeps_ids_distinct);
    RUN(test_fork_inherits_span_stack);
    std::printf("failures=%d\n", testing::failures());
    return testing::failures() == 0 ? 0 : 1;
}
