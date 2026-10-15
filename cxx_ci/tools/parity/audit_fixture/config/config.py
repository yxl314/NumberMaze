"""对照固件：一份最小工程的配置。

这是"项目配置"本身——它写下的值就是配置取值，因此两侧审计都不该把它当作
"配置旁路"（audit 里的 _is_config_source 会跳过它）。unused_flag 故意留成
没人引用的死配置，用来验证两侧的"死配置"判据同形。
"""

out_dir = "build/out"
release_tag = "v9.9"
unused_flag = "never-read"
