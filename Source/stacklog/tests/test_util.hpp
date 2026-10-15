#ifndef TESTS_TEST_UTIL_HPP
#define TESTS_TEST_UTIL_HPP

#include <cstdio>
#include <cstdlib>
#include <mutex>
#include <set>
#include <string>
#include <vector>

#include <stacklog/stacklog.hpp>

namespace testing
{

inline int& failures()
{
    static int count = 0;
    return count;
}

/// 把一条记录收集到内存里。write 由排空线程调用，故自带互斥。
class RecordingSink : public stacklog::Sink
{
public:
    RecordingSink() {}

    void write(const stacklog::Record& record)
    {
        std::lock_guard<std::mutex> lock(mutex_);
        records_.push_back(record);
    }

    void                          flush() {}

    std::vector<stacklog::Record> records() const
    {
        std::lock_guard<std::mutex> lock(mutex_);
        return records_;
    }

    std::size_t size() const
    {
        std::lock_guard<std::mutex> lock(mutex_);
        return records_.size();
    }

    void clear()
    {
        std::lock_guard<std::mutex> lock(mutex_);
        records_.clear();
    }

private:
    mutable std::mutex            mutex_;
    std::vector<stacklog::Record> records_;
};

/// 在记录集合里找指定操作名与种类的第一条记录。
inline bool
find_record(const std::vector<stacklog::Record>& records, const char* name, stacklog::Kind kind, stacklog::Record& out)
{
    for (std::size_t i = 0; i < records.size(); ++i)
    {
        if (std::string(records[i].t_name) == name && records[i].t_kind == kind)
        {
            out = records[i];
            return true;
        }
    }
    return false;
}

/// 记录集合里出现过的操作 id 个数与父引用是否都能解析。
inline std::size_t count_spans(const std::vector<stacklog::Record>& records)
{
    std::set<std::string> spans;
    for (std::size_t i = 0; i < records.size(); ++i)
    {
        spans.insert(stacklog::to_string(records[i].t_span));
    }
    return spans.size();
}

inline std::size_t count_orphans(const std::vector<stacklog::Record>& records)
{
    std::set<std::string> spans;
    for (std::size_t i = 0; i < records.size(); ++i)
    {
        spans.insert(stacklog::to_string(records[i].t_span));
    }

    std::size_t           orphans = 0;
    std::set<std::string> seen_parents;
    for (std::size_t i = 0; i < records.size(); ++i)
    {
        if (stacklog::is_null(records[i].t_parent))
        {
            continue;
        }
        const std::string parent = stacklog::to_string(records[i].t_parent);
        if (spans.count(parent) == 0 && seen_parents.insert(parent).second)
        {
            ++orphans;
        }
    }
    return orphans;
}

} // namespace testing

#define CHECK(condition)                                                                                               \
    do                                                                                                                 \
    {                                                                                                                  \
        if (!(condition))                                                                                              \
        {                                                                                                              \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #condition);                                           \
            ++testing::failures();                                                                                     \
        }                                                                                                              \
    }                                                                                                                  \
    while (0)

#define CHECK_EQ(actual, expected)                                                                                     \
    do                                                                                                                 \
    {                                                                                                                  \
        const auto actual_value   = (actual);                                                                          \
        const auto expected_value = (expected);                                                                        \
        if (!(actual_value == expected_value))                                                                         \
        {                                                                                                              \
            std::printf("FAIL %s:%d: %s == %s\n", __FILE__, __LINE__, #actual, #expected);                             \
            ++testing::failures();                                                                                     \
        }                                                                                                              \
    }                                                                                                                  \
    while (0)

#define RUN(test_function)                                                                                             \
    do                                                                                                                 \
    {                                                                                                                  \
        const int before = testing::failures();                                                                        \
        test_function();                                                                                               \
        std::printf("%-40s %s\n", #test_function, testing::failures() == before ? "ok" : "FAIL");                      \
    }                                                                                                                  \
    while (0)

#endif // TESTS_TEST_UTIL_HPP
