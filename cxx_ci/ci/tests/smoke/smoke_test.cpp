// googletest 1.8.1 冒烟工程：只用 C++98 语法。
//
// 仅在编译命令声明要求 C++98 时才校验标准模式。
// GCC 4.7 之前 __cplusplus 恒为 1，因此必须同时检查
// __GXX_EXPERIMENTAL_CXX0X__，不能只看 __cplusplus。

#if defined(SMOKE_REQUIRE_CXX98)
#  if defined(__GXX_EXPERIMENTAL_CXX0X__) || __cplusplus >= 201103L
#    error "冒烟工程要求以 C++98 模式编译"
#  endif
#endif

#include <gtest/gtest.h>

#include <stdexcept>
#include <string>
#include <vector>

TEST(Smoke, Arithmetic) {
  EXPECT_EQ(4, 2 + 2);
}

TEST(Smoke, Containers) {
  std::vector<int> values;
  values.push_back(1);
  values.push_back(2);
  ASSERT_EQ(2u, values.size());
  EXPECT_EQ(3, values[0] + values[1]);
}

TEST(Smoke, Strings) {
  std::string text("cxx_ci");
  EXPECT_STREQ("cxx_ci", text.c_str());
}

TEST(Smoke, Exceptions) {
  EXPECT_THROW(throw std::runtime_error("boom"), std::runtime_error);
}
