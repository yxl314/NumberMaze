# 标准在此显式声明：子目录不依赖父目录的隐式继承，
# 单独构建本目录时行为也一致。
set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(stacklog_test_ids test_ids.cpp)
target_link_libraries(stacklog_test_ids PRIVATE stacklog)

add_executable(stacklog_test_span test_span.cpp)
target_link_libraries(stacklog_test_span PRIVATE stacklog)

add_executable(stacklog_test_capture test_capture.cpp)
target_link_libraries(stacklog_test_capture PRIVATE stacklog)

add_executable(stacklog_test_lifecycle test_lifecycle.cpp)
target_link_libraries(stacklog_test_lifecycle PRIVATE stacklog)

add_executable(stacklog_test_stress test_stress.cpp)
target_link_libraries(stacklog_test_stress PRIVATE stacklog)

add_executable(stacklog_test_examples test_examples.cpp)
target_link_libraries(stacklog_test_examples PRIVATE stacklog)

foreach(target
	stacklog_test_ids
	stacklog_test_span
	stacklog_test_capture
	stacklog_test_lifecycle
	stacklog_test_stress
	stacklog_test_examples)
	add_test(NAME ${target} COMMAND ${target})
endforeach()

if(UNIX)
	add_executable(stacklog_test_fork test_fork.cpp)
	target_link_libraries(stacklog_test_fork PRIVATE stacklog)
	add_test(NAME stacklog_test_fork COMMAND stacklog_test_fork)
	set_tests_properties(stacklog_test_fork PROPERTIES TIMEOUT 60)
endif()

add_executable(stacklog_test_tree test_tree.cpp)
target_link_libraries(stacklog_test_tree PRIVATE stacklog)
add_test(NAME stacklog_test_tree COMMAND stacklog_test_tree)

# 编译期裁剪：同一份源码在两套阈值下各编译一次，两边都必须通过。
add_executable(stacklog_test_trim test_trim.cpp)
target_link_libraries(stacklog_test_trim PRIVATE stacklog)
add_test(NAME stacklog_test_trim COMMAND stacklog_test_trim)

add_executable(stacklog_test_trim_warn test_trim.cpp)
target_link_libraries(stacklog_test_trim_warn PRIVATE stacklog)
target_compile_definitions(stacklog_test_trim_warn PRIVATE
	STACKLOG_MIN_LEVEL=warn
	STACKLOG_MIN_LEVEL_IS_WARN=1)
add_test(NAME stacklog_test_trim_warn COMMAND stacklog_test_trim_warn)
