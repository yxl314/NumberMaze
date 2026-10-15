"""路径包含谓词与范围归因的单元测试（standardize 侧）。

与 ci 侧的 `tests/unit/test_paths.py` 是平行副本：两侧互不 import，靠同名同实现
的 `paths.py` 与这组对照测试守住同一口径。重点是软链接存在时的归属：各处自己
另写一遍前缀比较、归一化方式又不一致时，同一份诊断会被归成不同归属。
"""

import os

from cxx_standardize import paths, style


def symlink(target, link):
    os.symlink(str(target), str(link))


class _Config:
    """collect_files 只在 options 为空时才回退到配置，这里给个空壳即可。"""

    whitelist = ()
    blacklist = ()


class TestIsWithin:
    def test_same_path_is_within(self, tmp_path):
        assert paths.is_within(str(tmp_path), str(tmp_path))

    def test_strict_excludes_equal(self, tmp_path):
        assert not paths.is_within(str(tmp_path), str(tmp_path), strict=True)

    def test_sibling_with_common_prefix_is_not_within(self, tmp_path):
        root = tmp_path / "proj"
        sibling = tmp_path / "proj2"
        root.mkdir()
        sibling.mkdir()
        assert not paths.is_within(str(sibling / "a.cpp"), str(root))

    def test_symlinked_directory_resolves_to_its_target(self, tmp_path):
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        symlink(real, link)
        assert paths.is_within(str(link / "a.cpp"), str(real))
        assert paths.is_within(str(link / "a.cpp"), str(link))

    def test_third_party_symlinked_into_project_is_outside(self, tmp_path):
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


class TestCollectFilesExclude:
    def test_excluded_symlinked_dir_is_skipped(self, tmp_path):
        """被排除的目录是软链时，链下的文件也要按 realpath 排除掉。"""
        root = tmp_path / "proj"
        source = root / "src"
        source.mkdir(parents=True)
        (source / "a.cpp").write_text("int a;\n", encoding="utf-8")
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "b.cpp").write_text("int b;\n", encoding="utf-8")
        vendor = root / "vendor"
        vendor.mkdir()
        symlink(outside, vendor / "lib")

        options = style.Options(paths=[root], exclude=[vendor / "lib"])
        found = style.collect_files(_Config(), options)
        assert ["a.cpp"] == sorted(path.name for path in found)
