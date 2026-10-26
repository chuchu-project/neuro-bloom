#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""策略层：在「脑电 → 四键」的连续映射上面加一层氛围调制。

    policies.py —— 八套策略表（逐字抄自接入文档第 2 节，只加可用性标注）
    engine.py   —— 轮次 / 选策略 / 四键调制 / 回退开关

底层只负责「一动就变」，本层只负责「这一轮的总体氛围」，
**只能调制、不能替换** —— 否则画面就和脑电脱钩了。详见 engine.py 文件头。
"""
from __future__ import annotations

from .engine import (
    BREATH,
    DEFAULT_CONTROL_ROUNDS,
    DEFAULT_ROUND_SECONDS,
    StrategyEngine,
    control_rounds_from_env,
    enabled_from_env,
    modulate,
    round_seconds_from_env,
)
from .policies import (
    KNOB_VALUES,
    POLICIES,
    Policy,
    available_policies,
    check_table,
    get_policy,
    is_null_arm,
)

__all__ = [
    "BREATH",
    "DEFAULT_CONTROL_ROUNDS",
    "DEFAULT_ROUND_SECONDS",
    "KNOB_VALUES",
    "POLICIES",
    "Policy",
    "StrategyEngine",
    "available_policies",
    "check_table",
    "control_rounds_from_env",
    "enabled_from_env",
    "get_policy",
    "is_null_arm",
    "modulate",
    "round_seconds_from_env",
]
