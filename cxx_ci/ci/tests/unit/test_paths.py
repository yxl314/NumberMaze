#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""路径包含谓词与范围归因的单元测试。

只钉一件事：软链接存在时，全仓所有阶段必须给出同一个归属。分别按 realpath 与
abspath 判断时，同一份诊断在编译、运行、内存检查三个阶段归因相反——每一处单看
都对，合起来矛盾。这里把那道分界钉死，"顺手拼个前缀"会被审计的检查 5 拦下。
"""

import os

from lib import diagnostics, paths


def symlink(target, link):
    os.symlink(str(target), str(link))


def write(path, text):
    """写一个测试文件；显式指定编码，与工具自身的写法保持一致。"""
    handle = open(str(path), "w", encoding="utf-8")
    handle.write(text)
    handle.close()


class TestIsWithin:
    def test_same_path_is_within(self, tmp_path):
        assert paths.is_within(str(tmp_path), str(tmp_path))

    def test_strict_excludes_equal(self, tmp_path):
        assert not paths.is_within(str(tmp_path), str(tmp_path), strict=True)

    def test_child_is_within(self, tmp_path):
        child = tmp_path / "build" / "a.o"
        child.parent.mkdir()
        write(child, "x")
        assert paths.is_within(str(child), str(tmp_path))

    def test_sibling_with_common_prefix_is_not_within(self, tmp_path):
        """前缀相同但不是子目录：/proj2 不属于 /proj。"""
        root = tmp_path / "proj"
        sibling = tmp_path / "proj2"
        root.mkdir()
        sibling.mkdir()
        assert not paths.is_within(str(sibling / "a.cpp"), str(root))

    def test_symlinked_directory_resolves_to_its_target(self, tmp_path):
        """工程根是软链时，链下与目标下是同一个位置——两边都判"在"。"""
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        symlink(real, link)
        assert paths.is_within(str(link / "a.cpp"), str(real))
        assert paths.is_within(str(link / "a.cpp"), str(link))

    def test_third_party_symlinked_into_project_is_outside(self, tmp_path):
        """把第三方库软链进工程：解链后不在工程内，不能算项目自身。"""
        outside = tmp_path / "outside"
        outside.mkdir()
        project = tmp_path / "proj"
        (project / "vendor").mkdir(parents=True)
        symlink(outside, project / "vendor" / "lib")
        assert not paths.is_within(str(project / "vendor" / "lib" / "a.cpp"),
                                   str(project))


class TestIsWithinLogical:
    def test_symlinks_are_not_resolved(self, tmp_path):
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        symlink(real, link)
        assert paths.is_within_logical(str(link / "a.cpp"), str(link))
        assert not paths.is_within_logical(str(link / "a.cpp"), str(real))

    def test_same_path_is_within(self, tmp_path):
        assert paths.is_within_logical(str(tmp_path), str(tmp_path))

    def test_strict_excludes_equal(self, tmp_path):
        assert not paths.is_within_logical(str(tmp_path), str(tmp_path),
                                           strict=True)

    def test_sibling_with_common_prefix_is_not_within(self, tmp_path):
        root = tmp_path / "proj"
        sibling = tmp_path / "proj2"
        root.mkdir()
        sibling.mkdir()
        assert not paths.is_within_logical(str(sibling / "a.cpp"), str(root))


class TestScopeOf:
    def test_project(self, tmp_path):
        root = tmp_path / "proj"
        root.mkdir()
        assert "project" == paths.scope_of(str(root / "src" / "a.cpp"),
                                           [str(root)])

    def test_external(self, tmp_path):
        root = tmp_path / "proj"
        root.mkdir()
        assert "external" == paths.scope_of("/opt/qt/q.h", [str(root)])

    def test_excluded_wins_over_project(self, tmp_path):
        root = tmp_path / "proj"
        vendor = root / "vendor"
        vendor.mkdir(parents=True)
        assert "excluded" == paths.scope_of(
            str(vendor / "a.cpp"), [str(root)], [str(vendor)])

    def test_symlinked_third_party_is_external(self, tmp_path):
        """scope_of 也要解软链：软链进来的第三方目录判 external，不是 project。"""
        outside = tmp_path / "outside"
        outside.mkdir()
        project = tmp_path / "proj"
        (project / "vendor").mkdir(parents=True)
        symlink(outside, project / "vendor" / "lib")
        assert "external" == paths.scope_of(
            str(project / "vendor" / "lib" / "a.cpp"), [str(project)])


class TestAgreement:
    """diagnostics 的归属与 scope_of 必须一致——这正是 F1 收口要保住的口径。"""

    def test_symlinked_third_party_agrees(self, tmp_path):
        outside = tmp_path / "outside" / "lib"
        outside.mkdir(parents=True)
        write(outside / "a.cpp", "int a;\n")
        project = tmp_path / "proj"
        (project / "vendor").mkdir(parents=True)
        symlink(outside, project / "vendor" / "lib")
        target = str(project / "vendor" / "lib" / "a.cpp")
        assert "external" == paths.scope_of(target, [str(project)])
        assert not diagnostics.resolve_source(target, str(project))[1]

    def test_project_file_agrees(self, tmp_path):
        root = tmp_path / "proj"
        source = root / "src"
        source.mkdir(parents=True)
        write(source / "a.cpp", "int a;\n")
        target = str(source / "a.cpp")
        assert "project" == paths.scope_of(target, [str(root)])
        assert diagnostics.resolve_source(target, str(root))[1]


class TestSymlinkLayoutsAgreeEverywhere:
    """三种软链布局，四个消费点必须给出同一结论。

    四个消费点走的是同一条链：`clean` / `stages` / `valgrind` 用 is_within，
    `diagnostics` / `style` / `memcheck` 归因用 scope_of（它内部也委托
    is_within）。收口之前它们一个用 realpath、一个用 abspath，同一个文件会被
    判成"在构建树外"和"在项目内"两种相反结论——而每一处单独看都"对"。
    """

    def _verdicts(self, target, project_roots, excluded_roots=()):
        """四个消费点各自判"这个文件算不算项目自己的"。

        比的是**结论**而不是各自的措辞：scope_of 给 project/excluded/external
        三态，其余三处给布尔。断言的是它们对"算不算项目自己的"看法一致——
        这正是收口要保住的东西。收口前 clean 用 realpath、diagnostics 用
        abspath，同一路径会被判成相反结论。
        """
        scope = paths.scope_of(target, project_roots, excluded_roots)
        # clean / 阶段脚本的越界保护
        clean = any(paths.is_within(target, root) for root in project_roots)
        # diagnostics 的归属判定
        _resolved, owned = diagnostics.resolve_source(
            target, project_roots[0] if project_roots else "")
        # valgrind 的域内归因
        valgrind_inside = any(paths.is_within(target, root)
                              for root in project_roots)
        return {
            "scope": scope,
            "scope_owned": scope == "project",
            "clean": clean,
            "diagnostics": owned,
            "valgrind": valgrind_inside,
        }

    def test_build_dir_symlinked_outside_the_project(self, tmp_path):
        """构建目录是指向工程**外**的软链：它不算项目区。"""
        outside = tmp_path / "build-outside"
        outside.mkdir()
        project = tmp_path / "proj"
        project.mkdir()
        symlink(outside, project / "build")
        target = str(project / "build" / "obj" / "a.o")
        found = self._verdicts(target, [str(project)], [str(project / "build")])
        # 构建树被排进 excluded，四个消费点都得认它**不是**项目自己的。
        # 解链后它在工程外，所以 clean / diagnostics / valgrind 也都说 False
        assert "excluded" == found["scope"], found
        assert found["clean"] is False, found
        assert found["diagnostics"] is False, found
        assert found["valgrind"] is False, found

    def test_project_root_is_itself_a_symlink(self, tmp_path):
        """工程根自己是软链：解链前后必须给出同一结论（默认解链）。"""
        real = tmp_path / "real-project"
        (real / "src").mkdir(parents=True)
        write(real / "src" / "a.cpp", "int a;\n")
        link = tmp_path / "link-project"
        symlink(real, link)
        # 用软链路径访问同一份文件，结论应与真实路径一致
        via_link = str(link / "src" / "a.cpp")
        via_real = str(real / "src" / "a.cpp")
        assert paths.scope_of(via_link, [str(link)]) == \
            paths.scope_of(via_real, [str(real)])
        assert "project" == paths.scope_of(via_link, [str(link)])
        assert paths.is_within(via_link, str(link)) is True

    def test_third_party_symlinked_in_is_excluded_everywhere(self, tmp_path):
        """软链进来的第三方目录：scope_of 与 is_within 必须都判域外。"""
        outside = tmp_path / "elsewhere" / "lib"
        outside.mkdir(parents=True)
        write(outside / "a.cpp", "int a;\n")
        project = tmp_path / "proj"
        (project / "vendor").mkdir(parents=True)
        symlink(outside, project / "vendor" / "lib")
        target = str(project / "vendor" / "lib" / "a.cpp")
        found = self._verdicts(target, [str(project)],
                               [str(project / "vendor")])
        # 解链后这个文件既不在项目区也不在 excluded 区（软链指向两者之外），
        # 所以 scope 判 external。要紧的是**四处一致**：都不是"项目自己的"。
        # 收口前 diagnostics 用 abspath，会把软链路径当成 project/vendor 下，
        # 于是第三方头文件引发的诊断被算成项目自身问题判红
        assert found["scope"] in ("external", "excluded"), found
        assert found["scope_owned"] is False, found
        assert found["diagnostics"] is False, found
        assert found["clean"] is False, found
        assert found["valgrind"] is False, found

    def test_all_layouts_keep_the_four_consumers_consistent(self, tmp_path):
        """三种布局下，"算不算项目自己的"这一个问题四处答案必须相同。"""
        outside = tmp_path / "out"
        outside.mkdir()
        project = tmp_path / "proj"
        (project / "src").mkdir(parents=True)
        write(project / "src" / "a.cpp", "int a;\n")
        (project / "vendor").mkdir()
        symlink(outside, project / "vendor" / "lib")
        symlink(outside, project / "build")
        write(outside / "elsewhere.cpp", "int a;\n")

        layouts = [
            ("工程内源码", str(project / "src" / "a.cpp"), (project / "x",)),
            ("vendor 软链", str(project / "vendor" / "lib" / "e.cpp"), ()),
            ("构建目录软链", str(project / "build" / "e.cpp"), ()),
        ]
        for label, target, _extra in layouts:
            found = self._verdicts(target, [str(project)])
            answers = {found["scope_owned"], found["clean"],
                       found["diagnostics"], found["valgrind"]}
            assert 1 == len(answers), (
                "{}：四个消费点给出不一致的归属 {}".format(label, found))
