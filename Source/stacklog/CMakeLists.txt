cmake_minimum_required(VERSION 3.10)
project(stacklog
	VERSION 0.1.0
	DESCRIPTION "基于线程局部栈的调用链日志库"
	LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

include(GNUInstallDirs)
include(CMakePackageConfigHelpers)

find_package(Threads REQUIRED)

add_library(stacklog
	src/logger.cpp
	src/common/diagnose.cpp
	src/common/file.cpp
	src/common/time.cpp
	src/core/id.cpp
	src/core/record.cpp
	src/span/captured_span.cpp
	src/span/scoped_span.cpp
	src/io/record_file.cpp
	src/io/sink.cpp
	src/tree/render.cpp
	src/tree/tree_builder.cpp
	src/detail/context.cpp
	src/detail/ring.cpp
	src/detail/state.cpp
	src/detail/fork_guard.cpp)
add_library(stacklog::stacklog ALIAS stacklog)

target_include_directories(stacklog PUBLIC
	$<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
	$<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}>)
target_link_libraries(stacklog PUBLIC Threads::Threads)

if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
	target_compile_options(stacklog PRIVATE -Wall -Wextra -pedantic)
endif()

# 分析工具：读结构化记录文件，建树并输出。独立于库的日常使用。
add_executable(stacklog_tree tool/stacklog_tree.cpp)
target_link_libraries(stacklog_tree PRIVATE stacklog)

# 让使用者可以 find_package(stacklog) 并链接 stacklog::stacklog。
install(TARGETS stacklog stacklog_tree
	EXPORT stacklogTargets
	RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR}
	LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
	ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR})
install(DIRECTORY include/ DESTINATION ${CMAKE_INSTALL_INCLUDEDIR})

install(EXPORT stacklogTargets
	FILE stacklogTargets.cmake
	NAMESPACE stacklog::
	DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/stacklog)

configure_package_config_file(
	${CMAKE_CURRENT_SOURCE_DIR}/cmake/stacklogConfig.cmake.in
	${CMAKE_CURRENT_BINARY_DIR}/stacklogConfig.cmake
	INSTALL_DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/stacklog)

write_basic_package_version_file(
	${CMAKE_CURRENT_BINARY_DIR}/stacklogConfigVersion.cmake
	VERSION ${PROJECT_VERSION}
	COMPATIBILITY SameMajorVersion)

install(FILES
	${CMAKE_CURRENT_BINARY_DIR}/stacklogConfig.cmake
	${CMAKE_CURRENT_BINARY_DIR}/stacklogConfigVersion.cmake
	DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/stacklog)

option(STACKLOG_BUILD_TESTS "Build stacklog tests" ON)

if(STACKLOG_BUILD_TESTS)
	enable_testing()
	add_subdirectory(tests)
endif()
