// 守卫用例：独占、次实例、释放后可再取、残留清理、一问一答、
// 请求内容、上限、路径过长、非主实例服务、并发启动。
#include <cstdio>
#include <cstring>
#include <string>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>

#include "test_util.hpp"

#include <singleproc/singleproc.hpp>

namespace
{

std::string dir()
{
    return "/tmp/singleproc_test";
}

void prepare(const char* v_name)
{
    ::mkdir(dir().c_str(), 0700);
    ::unlink((dir() + "/" + v_name + ".lock").c_str());
    ::unlink((dir() + "/" + v_name + ".sock").c_str());
}

singleproc::Options options_for(const char* v_name)
{
    singleproc::Options options;
    options.t_name               = v_name;
    options.t_directory          = dir();
    options.t_connect_timeout_ms = 200;
    options.t_request_timeout_ms = 300;
    return options;
}

/// 收取请求内容并回固定应答。
singleproc::OnRequest echo_back(const char* v_answer, std::string* v_seen)
{
    return [v_answer, v_seen](const std::string& v_request) -> std::string
    {
        if (v_seen != 0)
        {
            *v_seen = v_request;
        }
        return v_answer;
    };
}

void test_first_is_primary()
{
    prepare("a");
    const singleproc::Options options = options_for("a");
    singleproc::Guard         guard(options, 0, 0);
    CHECK_EQ(guard.status(), singleproc::Status::Primary);
    CHECK(guard.is_primary());
    CHECK_EQ(std::string(guard.message()), std::string("正常"));
}

void test_second_is_secondary()
{
    prepare("b");
    const singleproc::Options options = options_for("b");
    singleproc::Guard         first(options, 0, 0);
    CHECK(first.is_primary());

    // 没有交互意图（既没给请求，也没给应答回调）：不白等超时，直接报"被别人持有"
    singleproc::Guard second(options, 0, 0);
    CHECK(!second.is_primary());
    CHECK_EQ(second.status(), singleproc::Status::OwnedByOther);
    CHECK_EQ(second.status(), singleproc::Status::OwnedByOther);
}

void test_release_allows_next()
{
    prepare("c");
    const singleproc::Options options = options_for("c");
    {
        singleproc::Guard first(options, 0, 0);
        CHECK(first.is_primary());
    }
    // 前一个析构后应能重新取得独占权
    singleproc::Guard second(options, 0, 0);
    CHECK(second.is_primary());
}

void test_dead_socket_file_is_reclaimed()
{
    prepare("d");
    const singleproc::Options options = options_for("d");
    // 造一个死残留：套接字文件在，但没有任何进程在监听
    const std::string         sock    = dir() + "/d.sock";
    FILE*                     file    = std::fopen(sock.c_str(), "wb");
    CHECK(file != 0);
    if (file != 0)
    {
        std::fclose(file);
    }

    singleproc::Guard guard(options, 0, 0);
    CHECK(guard.is_primary()); // 残留被识别并清理
}

void test_primary_deletes_its_own_socket()
{
    prepare("e");
    const singleproc::Options options = options_for("e");
    const std::string         sock    = dir() + "/e.sock";
    {
        singleproc::Guard guard(options, 0, 0);
        CHECK(guard.is_primary());
        struct stat info;
        CHECK_EQ(::stat(sock.c_str(), &info), 0);
    }
    struct stat info;
    CHECK(::stat(sock.c_str(), &info) != 0); // 本人建立，本人清理
}

void test_secondary_does_not_delete_socket()
{
    prepare("f");
    const singleproc::Options options = options_for("f");
    const std::string         sock    = dir() + "/f.sock";
    singleproc::Guard         primary(options, 0, 0);
    CHECK(primary.is_primary());
    {
        // 次实例从未建立监听，析构时不得动主实例的套接字文件
        singleproc::Guard secondary(options, 0, 0);
        CHECK(!secondary.is_primary());
    }
    struct stat info;
    CHECK_EQ(::stat(sock.c_str(), &info), 0);
}

void test_request_and_response_reach_both_sides()
{
    prepare("g");
    singleproc::Options options = options_for("g");
    options.t_request           = "status";

    // 主实例的服务循环在子进程里跑，主实例看到什么必须跨进程传回来，
    // 因此写文件再核对，而不是用父子各自独立的内存副本。
    const std::string seen_path = dir() + "/g.v_seen";
    ::unlink(seen_path.c_str());

    singleproc::OnRequest on_request = [&seen_path](const std::string& v_request) -> std::string
    {
        FILE* file = std::fopen(seen_path.c_str(), "wb");
        if (file != 0)
        {
            std::fwrite(v_request.data(), 1, v_request.size(), file);
            std::fclose(file);
        }
        return "running";
    };

    singleproc::Guard primary(options, on_request, 0);
    CHECK(primary.is_primary());

    const pid_t worker = ::fork();
    if (worker == 0)
    {
        for (int i = 0; i < 8; ++i)
        {
            primary.serve_once();
        }
        ::_exit(0);
    }
    usleep(150000);

    std::string            secondary_saw;
    singleproc::OnResponse on_response = [&secondary_saw](const std::string& v_response) -> bool
    {
        secondary_saw = v_response;
        return true;
    };

    singleproc::Guard secondary(options, 0, on_response);
    CHECK(!secondary.is_primary());
    CHECK_EQ(secondary.status(), singleproc::Status::Secondary);
    CHECK_EQ(secondary.request(), std::string("status"));
    CHECK_EQ(secondary_saw, std::string("running"));

    ::kill(worker, SIGKILL);
    ::waitpid(worker, 0, 0);

    // 主实例回调收到的请求内容，从文件读回核对
    std::string primary_saw;
    FILE*       seen = std::fopen(seen_path.c_str(), "rb");
    CHECK(seen != 0);
    if (seen != 0)
    {
        char              buffer[64];
        const std::size_t got = std::fread(buffer, 1, sizeof(buffer), seen);
        primary_saw.assign(buffer, got);
        std::fclose(seen);
    }
    CHECK_EQ(primary_saw, std::string("status"));
}

void test_message_limit_by_request()
{
    prepare("h");
    singleproc::Options options = options_for("h");
    options.t_max_message_bytes = 4;
    options.t_request           = "way too long for the limit";

    singleproc::Guard primary(options, echo_back("ok", 0), 0);
    CHECK(primary.is_primary());

    const pid_t worker = ::fork();
    if (worker == 0)
    {
        for (int i = 0; i < 8; ++i)
        {
            primary.serve_once();
        }
        ::_exit(0);
    }
    usleep(150000);

    // 请求超过上限：发不出去，状态明确
    singleproc::Guard secondary(options, 0, 0);
    CHECK_EQ(secondary.status(), singleproc::Status::MessageTooLarge);

    ::kill(worker, SIGKILL);
    ::waitpid(worker, 0, 0);
}

void test_path_too_long()
{
    prepare("i");
    singleproc::Options options = options_for("i");
    options.t_name              = std::string(300, 'x'); // 远超 sun_path 限制
    singleproc::Guard guard(options, 0, 0);
    CHECK_EQ(guard.status(), singleproc::Status::PathTooLong);
    CHECK(!guard.is_primary());
}

void test_empty_name_rejected()
{
    prepare("j");
    singleproc::Options options = options_for("j");
    options.t_name.clear();
    singleproc::Guard guard(options, 0, 0);
    CHECK(!guard.is_primary());
    CHECK_EQ(guard.status(), singleproc::Status::FailedLock);
}

void test_serve_once_on_secondary()
{
    prepare("k");
    const singleproc::Options options = options_for("k");
    singleproc::Guard         primary(options, 0, 0);
    CHECK(primary.is_primary());

    singleproc::Guard secondary(options, 0, 0);
    CHECK(!secondary.is_primary());
    // 次实例不能服务：明确返回，不做任何读写
    CHECK_EQ(secondary.serve_once(), singleproc::Status::NotPrimary);
}

void test_directory_is_queryable()
{
    prepare("m");
    const singleproc::Options options = options_for("m");
    singleproc::Guard         guard(options, 0, 0);
    CHECK(guard.is_primary());
    // 调用方指定了目录：应当原样反映，且不算回退
    CHECK_EQ(guard.directory(), dir());
    CHECK(!guard.used_fallback_directory());
}

void test_fallback_directory_is_visible()
{
    prepare("n");
    singleproc::Options options = options_for("n");
    options.t_directory.clear();       // 交由环境推导
    singleproc::Guard guard(options, 0, 0);
    CHECK(guard.is_primary());
    // 实际目录必须可查，回退状态必须可见——退到共享目录不能是无声的
    CHECK(!guard.directory().empty());
    const bool expects_fallback = ::getenv("XDG_RUNTIME_DIR") == 0;
    CHECK_EQ(guard.used_fallback_directory(), expects_fallback);
    ::unlink((guard.directory() + "/n.lock").c_str());
    ::unlink((guard.directory() + "/n.sock").c_str());
}

void test_concurrent_start_single_winner()
{
    prepare("l");
    const singleproc::Options options  = options_for("l");

    const int                 children = 4;
    std::vector<pid_t>        pids;
    for (int i = 0; i < children; ++i)
    {
        const pid_t pid = ::fork();
        if (pid == 0)
        {
            singleproc::Guard guard(options, 0, 0);
            // 成为主实例者短暂持有，保证竞争真实发生
            if (guard.is_primary())
            {
                usleep(200000);
            }
            ::_exit(guard.is_primary() ? 0 : 1);
        }
        pids.push_back(pid);
    }

    int primary_count = 0;
    for (std::size_t i = 0; i < pids.size(); ++i)
    {
        int status = 0;
        ::waitpid(pids[i], &status, 0);
        if (WIFEXITED(status) && WEXITSTATUS(status) == 0)
        {
            ++primary_count;
        }
    }
    CHECK_EQ(primary_count, 1);
}

} // namespace

int main()
{
    RUN(test_first_is_primary);
    RUN(test_second_is_secondary);
    RUN(test_release_allows_next);
    RUN(test_dead_socket_file_is_reclaimed);
    RUN(test_primary_deletes_its_own_socket);
    RUN(test_secondary_does_not_delete_socket);
    RUN(test_request_and_response_reach_both_sides);
    RUN(test_message_limit_by_request);
    RUN(test_path_too_long);
    RUN(test_empty_name_rejected);
    RUN(test_serve_once_on_secondary);
    RUN(test_directory_is_queryable);
    RUN(test_fallback_directory_is_visible);
    RUN(test_concurrent_start_single_winner);
    return testing::failures() == 0 ? 0 : 1;
}
