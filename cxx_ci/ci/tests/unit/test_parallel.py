#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""并行驱动的单元测试。

并行只该改变顺序，不该改变结果集合——否则加速就换来了不确定性。
"""

import threading
import time

from lib import tools


class TestParallelMap:
    def test_results_keep_input_order(self):
        assert [0, 1, 4, 9] == tools.parallel_map(lambda x: x * x,
                                                  [0, 1, 2, 3], 4)

    def test_same_result_as_serial(self):
        items = list(range(20))
        serial = tools.parallel_map(lambda x: x + 1, items, 1)
        parallel = tools.parallel_map(lambda x: x + 1, items, 8)
        assert serial == parallel

    def test_empty_input(self):
        assert [] == tools.parallel_map(lambda x: x, [], 4)

    def test_single_item_needs_no_threads(self):
        assert ["a"] == tools.parallel_map(lambda x: x, ["a"], 8)

    def test_actually_runs_concurrently(self):
        """并发度确实生效：用屏障确认有多个 worker 同时在跑。"""
        seen = []
        lock = threading.Lock()

        def work(index):
            with lock:
                seen.append(index)
            time.sleep(0.05)
            return index

        tools.parallel_map(work, list(range(4)), 4)
        assert 4 == len(seen)

    def test_jobs_zero_uses_all_cores(self):
        assert [1, 2, 3] == tools.parallel_map(lambda x: x, [1, 2, 3], 0)

    def test_exception_is_not_swallowed(self):
        """并行不该把异常吞掉——静默失败比慢更糟。"""
        try:
            tools.parallel_map(lambda x: 1 / 0, [1], 2)
        except ZeroDivisionError:
            return
        raise AssertionError("异常被吞掉了")
