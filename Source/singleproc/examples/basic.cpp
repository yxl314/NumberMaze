// 最简用法：确保同一时刻只有一个实例在跑。
#include <cstdio>
#include <cstring>

#include <singleproc/singleproc.hpp>

int main(int argc, char** argv)
{
    singleproc::Options options;
    options.t_name = "singleproc-demo";
    if (argc > 1)
    {
        options.t_directory = argv[1];
    }

    bool              running = true;
    singleproc::Guard guard(
        options,
        // 主实例：来了新实例时怎么办，由这里决定。库不看内容。
        [](const std::string& v_request) -> std::string
        {
            std::printf("  主实例收到请求: %s\n", v_request.c_str());
            if (v_request == "status")
            {
                return "running";
            }
            return "busy";
        },
        // 次实例：拿到应答后决定自己还起不起。
        [&running](const std::string& v_response) -> bool
        {
            std::printf("  次实例收到应答: %s\n", v_response.c_str());
            running = v_response == "running"; // 主实例在跑，自己就不跑了
            return running;
        });

    std::printf("状态: %s（%s）\n", singleproc::status_name(guard.status()), guard.message());

    if (!guard.is_primary())
    {
        return 0;
    }

    std::printf("本实例独占运行，按回车退出\n");
    std::fflush(stdout);
    // 主循环里接受连接；实际项目通常放在自己的线程或事件循环中。
    while (running)
    {
        guard.serve_once();
        if (std::getchar() == EOF)
        {
            break;
        }
    }
    return 0;
}
