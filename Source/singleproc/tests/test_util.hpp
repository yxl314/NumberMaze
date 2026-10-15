#ifndef SINGLEPROC_TEST_UTIL_HPP
#define SINGLEPROC_TEST_UTIL_HPP

#include <cstdio>

namespace testing
{

inline int& failures()
{
    static int count = 0;
    return count;
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
        std::printf("%-44s %s\n", #test_function, testing::failures() == before ? "ok" : "FAIL");                      \
    }                                                                                                                  \
    while (0)

#endif // SINGLEPROC_TEST_UTIL_HPP
