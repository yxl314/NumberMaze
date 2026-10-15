"""对照固件：一份引用全部配置键的源码。

两份 audit.py 的配置对象键名并不完全相同（ci 侧有 build_file/std_pin/std_vars，
standardize 侧有 cxx_standard/tidy_header_filter），这里把双方的键都读一遍，
使"死配置"在两侧都只剩那个真的没人用的 unused_flag。返回元组末尾的字面量
v9.9 与 release_tag 取值相同，用来验证两侧的"配置旁路"判据。
"""

def inspect(cfg):
    return (cfg.root, cfg.build_file, cfg.std_pin, cfg.std_vars,
            cfg.cxx_standard, cfg.tidy_header_filter,
            cfg.out_dir, cfg.release_tag, "v9.9")
