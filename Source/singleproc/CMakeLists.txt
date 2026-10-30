cmake_minimum_required(VERSION 3.10)
project(singleproc
	VERSION 0.1.0
	DESCRIPTION "同一系统内只允许一个实例运行的 RAII 守卫"
	LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

include(GNUInstallDirs)
include(CMakePackageConfigHelpers)

if(NOT CMAKE_BUILD_TYPE)
	set(CMAKE_BUILD_TYPE Release)
endif()

add_library(singleproc
	src/guard.cpp
	src/platform/platform_posix.cpp)
add_library(singleproc::singleproc ALIAS singleproc)

target_include_directories(singleproc PUBLIC
	$<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
	$<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}>)
target_include_directories(singleproc PRIVATE
	${CMAKE_CURRENT_SOURCE_DIR}/src)

if(UNIX)
	target_compile_options(singleproc PRIVATE -Wall -Wextra -pedantic)
endif()

# 让使用者可以 find_package(singleproc) 并链接 singleproc::singleproc。
install(TARGETS singleproc
	EXPORT singleprocTargets
	RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR}
	LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
	ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR})
install(DIRECTORY include/ DESTINATION ${CMAKE_INSTALL_INCLUDEDIR})

install(EXPORT singleprocTargets
	FILE singleprocTargets.cmake
	NAMESPACE singleproc::
	DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/singleproc)

configure_package_config_file(
	${CMAKE_CURRENT_SOURCE_DIR}/cmake/singleprocConfig.cmake.in
	${CMAKE_CURRENT_BINARY_DIR}/singleprocConfig.cmake
	INSTALL_DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/singleproc)

write_basic_package_version_file(
	${CMAKE_CURRENT_BINARY_DIR}/singleprocConfigVersion.cmake
	VERSION ${PROJECT_VERSION}
	COMPATIBILITY SameMajorVersion)

install(FILES
	${CMAKE_CURRENT_BINARY_DIR}/singleprocConfig.cmake
	${CMAKE_CURRENT_BINARY_DIR}/singleprocConfigVersion.cmake
	DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/singleproc)

option(SINGLEPROC_BUILD_TESTS "构建测试" ON)
option(SINGLEPROC_BUILD_EXAMPLES "构建示例" ON)

if(SINGLEPROC_BUILD_EXAMPLES)
	add_executable(singleproc_example_basic examples/basic.cpp)
	target_link_libraries(singleproc_example_basic PRIVATE singleproc::singleproc)
endif()

if(SINGLEPROC_BUILD_TESTS)
	enable_testing()
	add_subdirectory(tests)
endif()
