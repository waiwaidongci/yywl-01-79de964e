from __future__ import annotations

import json
import re
from pathlib import Path

import pytest


def test_test_sh_is_executable():
    """题面：「程序入口是 `/app/route_model.py`」"""
    script = Path(__file__).with_name("test.sh")
    assert script.exists()
    assert script.stat().st_mode & 0o777 == 0o755


def test_verifier_hard_timeout_leaves_cleanup_margin():
    """题面：「达到这个规模的一批数据需要在 40 秒内完成」"""
    test_sh = Path(__file__).with_name("test.sh").read_text(encoding="utf-8")
    kill_after = int(re.search(r"--kill-after=(\d+)s", test_sh).group(1))
    watchdog = int(re.search(r"--kill-after=\d+s (\d+)s", test_sh).group(1))
    assert kill_after == 5
    assert kill_after < watchdog


def test_verifier_completion_report_is_structured():
    """题面：「任何失败路径都不得打印 traceback、改写输入文件或发布半成品。」"""
    text = Path(__file__).with_name("test.sh").read_text(encoding="utf-8")
    assert "verification-status.txt" in text
    assert "INCOMPLETE:" in text
    assert "COMPLETED:" in text
    assert "scoring-report.json" in text
    assert "--junitxml" in text


def test_scoring_policy_uses_business_priority():
    """题面：「把多次回放合成一份可重复的阶段表和吸收链模型。」"""
    config = json.loads(Path(__file__).with_name("scoring.json").read_text())
    core = [
        item
        for item in config["tests"].values()
        if item["priority"] == "core"
    ]
    non_core = [
        item
        for item in config["tests"].values()
        if item["priority"] != "core"
    ]
    assert config["weight_policy"] == {
        "version": 1,
        "basis": "business_priority",
        "core_min_share": 0.5,
    }
    assert sum(
        item["weight"] for item in config["tests"].values()
    ) == pytest.approx(1.0)
    assert sum(item["weight"] for item in core) >= 0.5
    assert min(item["weight"] for item in core) > max(
        item["weight"] for item in non_core
    )
