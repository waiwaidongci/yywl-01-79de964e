from __future__ import annotations

import json
import time
from decimal import Decimal

import pytest

from helpers import (
    bad_case,
    break_support,
    canonicalized_model,
    expected_model,
    file_mode,
    force_rank_median_tie,
    generate_batch,
    load_model,
    model_is_canonical,
    route_row_error,
    run_batch,
    run_candidate,
    symmetric_batch,
    write_case_value,
    write_json,
)


@pytest.mark.case_ids("CORR-ROLE", "REPLAY-INVARIANT")
def test_aligns_unlabeled_runs_by_route_structure(tmp_path):
    """题面：「这些回放描述的是同一条流程，只是本地短号各自独立。」"""
    batch, truth = generate_batch(size=6, runs=3, topology_seed=2104)
    result, output, _ = run_batch(tmp_path, batch)
    model = load_model(output)
    case_ids = ("CORR-ROLE", "REPLAY-INVARIANT")
    assert result.returncode == 0
    assert result.stderr == ""
    assert canonicalized_model(model) == expected_model(batch, truth)
    assert case_ids == ("CORR-ROLE", "REPLAY-INVARIANT")


def test_local_ids_are_not_global_stage_keys(tmp_path):
    """题面：「同一个业务阶段在五次回放里可能叫完全不同的名字，节点短号之间也没有迁移对照表。」"""
    first, _ = generate_batch(size=6, runs=3, id_seed=3301)
    second, second_truth = generate_batch(size=6, runs=3, id_seed=7717)
    second["runs"].reverse()
    second_truth["mappings"].reverse()
    result_a, output_a, _ = run_batch(tmp_path, first, "model-a.json")
    result_b, output_b, _ = run_batch(tmp_path, second, "model-b.json")
    raw = output_a.read_bytes() + output_b.read_bytes()
    assert result_a.returncode == 0
    assert result_b.returncode == 0
    assert output_a.read_bytes() == output_b.read_bytes()
    assert b"N-" not in raw


def test_service_class_participates_in_alignment(tmp_path):
    """题面：「相同业务阶段在各回放里的 `service_class` 相同；非零转移关系也相同」"""
    batch, _ = generate_batch(size=6, runs=3)
    batch["runs"][0]["nodes"][0]["service_class"] = (
        batch["runs"][0]["nodes"][0]["service_class"] + 1
    ) % 4
    result, output, _ = run_batch(tmp_path, batch)
    assert result.returncode == 3
    assert result.stderr == "route model refused: " + "unsupported_model\n"
    assert output.exists() is False


def test_rejects_inconsistent_nonzero_edge_support(tmp_path):
    """题面：「若两个阶段具有相同根化角色，或不同回放无法在保持服务类别和非零边关系的前提下对齐，这组数据不能形成可交付模型。」"""
    batch, _ = generate_batch(size=7, runs=3)
    result, output, _ = run_batch(tmp_path, break_support(batch))
    assert result.returncode == 3
    assert result.stderr == "route model refused: " + "unsupported_model\n"
    assert output.exists() is False


def test_rejects_nonunique_rooted_roles(tmp_path):
    """题面：「若两个阶段具有相同根化角色，或不同回放无法在保持服务类别和非零边关系的前提下对齐，这组数据不能形成可交付模型。」"""
    result, output, _ = run_batch(tmp_path, symmetric_batch())
    assert result.returncode == 3
    assert result.stderr == "route model refused: " + "unsupported_model\n"
    assert output.exists() is False


@pytest.mark.case_ids("CANON-02", "CANON-ORDER")
def test_canonical_stage_order_from_rooted_roles(tmp_path):
    """题面：「输出阶段必须按各自根化流程角色得到一个规范顺序，依次编号 `S01`、`S02`，直到最后一个阶段。」"""
    batch, truth = generate_batch(size=8, runs=4, topology_seed=8101)
    result, output, _ = run_batch(tmp_path, batch)
    model = load_model(output)
    case_ids = ("CANON-02", "CANON-ORDER")
    assert result.returncode == 0
    assert (
        canonicalized_model(model)["stages"]
        == expected_model(batch, truth)["stages"]
    )
    assert model_is_canonical(model)
    assert case_ids == ("CANON-02", "CANON-ORDER")


@pytest.mark.case_ids("CANON-03", "FRESH-01", "CANON-LOCAL")
def test_run_and_local_id_permutations_are_byte_identical(tmp_path):
    """题面：「只要两个输入只差回放顺序或本地短号改名，输出 JSON 的字节必须一致。」"""
    first, _ = generate_batch(size=8, runs=4, id_seed=7001)
    second, _ = generate_batch(size=8, runs=4, id_seed=9901)
    second["runs"].reverse()
    result_a, output_a, _ = run_batch(tmp_path, first, "one.json")
    result_b, output_b, _ = run_batch(tmp_path, second, "two.json")
    case_ids = ("CANON-03", "FRESH-01", "CANON-LOCAL")
    assert result_a.returncode == 0
    assert result_b.returncode == 0
    assert output_a.read_bytes() == output_b.read_bytes()
    assert case_ids == ("CANON-03", "FRESH-01", "CANON-LOCAL")


def test_aggregates_visits_entries_exits_and_edges(tmp_path):
    """题面：「相同阶段的访问量、入口数、出口数和边计数分别相加」"""
    batch, truth = generate_batch(size=7, runs=4)
    result, output, _ = run_batch(tmp_path, batch)
    model = load_model(output)
    expected = expected_model(batch, truth)
    assert result.returncode == 0
    normalized = canonicalized_model(model)
    assert normalized["stages"] == expected["stages"]
    assert normalized["entries"] == expected["entries"]
    assert normalized["exits"] == expected["exits"]
    assert normalized["transitions"] == expected["transitions"]


@pytest.mark.case_ids("AGG-MEDIAN", "PARAM-ROUND")
def test_merges_service_samples_for_global_median(tmp_path):
    """题面：「中位服务时长由该阶段在全部回放中的 `service_samples` 合并计算。」"""
    batch, truth = generate_batch(size=6, runs=5, flow_seed=4410)
    force_rank_median_tie(batch, truth, 0, 100, 101)
    result, output, _ = run_batch(tmp_path, batch)
    model = load_model(output)
    expected = expected_model(batch, truth)
    case_ids = ("AGG-MEDIAN", "PARAM-ROUND")
    assert result.returncode == 0
    assert [
        item["median_service_ms"]
        for item in canonicalized_model(model)["stages"]
    ] == [
        item["median_service_ms"] for item in expected["stages"]
    ]
    assert case_ids == ("AGG-MEDIAN", "PARAM-ROUND")


def test_entry_distribution_uses_aggregate_counts(tmp_path):
    """题面：「入口概率是某阶段的合计入口数除以全部入口数。」"""
    batch, truth = generate_batch(size=6, runs=4, flow_seed=5501)
    result, output, _ = run_batch(tmp_path, batch)
    model = load_model(output)
    assert result.returncode == 0
    assert (
        canonicalized_model(model)["entries"]
        == expected_model(batch, truth)["entries"]
    )


def test_exit_probability_uses_outgoing_denominator(tmp_path):
    """题面：「出口概率是某阶段的合计出口数除以该阶段全部离开数，分母为该阶段的后继边合计与出口数之和。」"""
    batch, truth = generate_batch(size=6, runs=3)
    result, output, _ = run_batch(tmp_path, batch)
    model = load_model(output)
    expected = expected_model(batch, truth)
    assert result.returncode == 0
    assert canonicalized_model(model)["exits"] == expected["exits"]
    assert route_row_error(model) <= Decimal("0.000001")


def test_transition_probabilities_use_global_support(tmp_path):
    """题面：「转移概率按起点阶段分别归一，分母相同。」"""
    batch, truth = generate_batch(size=7, runs=5, flow_seed=7703)
    result, output, _ = run_batch(tmp_path, batch)
    model = load_model(output)
    assert result.returncode == 0
    assert (
        canonicalized_model(model)["transitions"]
        == expected_model(batch, truth)["transitions"]
    )


def test_expected_visits_solve_fundamental_matrix(tmp_path):
    """题面：「以入口分布作为初始行向量，计算每个阶段的期望访问次数」"""
    batch, truth = generate_batch(size=6, runs=3)
    result, output, _ = run_batch(tmp_path, batch)
    model = load_model(output)
    expected = expected_model(batch, truth)
    assert result.returncode == 0
    assert (
        canonicalized_model(model)["calibration"]["expected_visits"]
        == expected["calibration"]["expected_visits"]
    )


def test_expected_cycle_service_matches_projection(tmp_path):
    """题面：「再乘该阶段中位服务时长并求和，得到 `expected_cycle_service_ms`。」"""
    batch, truth = generate_batch(size=7, runs=3)
    result, output, _ = run_batch(tmp_path, batch)
    model = load_model(output)
    expected = expected_model(batch, truth)
    assert result.returncode == 0
    assert (
        canonicalized_model(model)["calibration"]["expected_cycle_service_ms"]
        == expected["calibration"]["expected_cycle_service_ms"]
    )


@pytest.mark.case_ids("SUPPORT-RATIO", "SUPPORT-COMPLETE")
def test_support_ratio_and_aligned_runs(tmp_path):
    """题面：「`support_ratio` 是成功参与联合对齐的节点访问量除以全部回放节点访问量。」"""
    batch, truth = generate_batch(size=7, runs=5)
    result, output, _ = run_batch(tmp_path, batch)
    model = load_model(output)
    expected = expected_model(batch, truth)
    case_ids = ("SUPPORT-RATIO", "SUPPORT-COMPLETE")
    assert result.returncode == 0
    assert model["calibration"]["aligned_runs"] == 5
    assert model["calibration"]["support_ratio"] == pytest.approx(1.0)
    assert sum(item["visits"] for item in model["stages"]) == sum(
        item["visits"] for item in expected["stages"]
    )
    assert case_ids == ("SUPPORT-RATIO", "SUPPORT-COMPLETE")


def test_batch_id_is_copied_literally(tmp_path):
    """题面：「批次号原样抄回输入」"""
    batch, _ = generate_batch(size=5, runs=2)
    result, output, _ = run_batch(tmp_path, batch)
    model = load_model(output)
    assert result.returncode == 0
    assert model["batch_id"] == batch["batch_id"]


def test_output_uses_compact_sorted_json_bytes(tmp_path):
    """题面：「输出文件采用 UTF-8 紧凑 JSON：键按字典序排列，分隔符为逗号和冒号且不带空格」"""
    batch, _ = generate_batch(size=5, runs=3)
    result, output, input_raw = run_batch(tmp_path, batch)
    raw = output.read_bytes()
    parsed = json.loads(raw)
    expected = (
        json.dumps(parsed, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    assert result.returncode == 0
    assert raw == expected
    assert (tmp_path / "batch.json").read_bytes() == input_raw


def test_output_has_final_newline_and_mode(tmp_path):
    """题面：「文件末尾恰好一个换行，权限为 `0644`。」"""
    batch, _ = generate_batch(size=5, runs=2)
    result, output, _ = run_batch(tmp_path, batch)
    assert result.returncode == 0
    assert output.read_bytes().endswith(b"\n")
    assert not output.read_bytes().endswith(b"\n\n")
    assert file_mode(output) == 0o644


def test_success_streams_are_empty(tmp_path):
    """题面：「运行成功时不向标准输出或标准错误写任何内容。」"""
    batch, _ = generate_batch(size=5, runs=2)
    result, _, _ = run_batch(tmp_path, batch)
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


@pytest.mark.parametrize(
    "case",
    [
        "SCHEMA-TOP-MISSING",
        "SCHEMA-TOP-EXTRA",
        "SCHEMA-RUN-MISSING",
        "SCHEMA-RUN-EXTRA",
        "SCHEMA-NODE-MISSING",
        "SCHEMA-NODE-EXTRA",
        "SCHEMA-EDGE-MISSING",
        "SCHEMA-EDGE-EXTRA",
        "SCHEMA-TYPE-ENUM",
        "SCHEMA-DUPLICATE",
    ],
)
def test_strict_schema_refuses_equivalent_bad_shapes(tmp_path, case):
    """题面：「同名键在任一层重复出现时，整份批次作废。」"""
    batch, _ = generate_batch(size=5, runs=2)
    value, reason = bad_case(batch, case)
    input_path = tmp_path / "batch.json"
    output_path = tmp_path / "model.json"
    write_case_value(input_path, value)
    result = run_candidate(input_path, output_path)
    case_ids = (
        "SCHEMA-TOP-MISSING",
        "SCHEMA-TOP-EXTRA",
        "SCHEMA-RUN-MISSING",
        "SCHEMA-RUN-EXTRA",
        "SCHEMA-NODE-MISSING",
        "SCHEMA-NODE-EXTRA",
        "SCHEMA-EDGE-MISSING",
        "SCHEMA-EDGE-EXTRA",
        "SCHEMA-TYPE-ENUM",
        "SCHEMA-DUPLICATE",
    )
    assert result.returncode == 3
    assert result.stdout == ""
    assert result.stderr == "route model refused: " + reason + "\n"
    assert output_path.exists() is False
    assert case in case_ids


@pytest.mark.parametrize(
    ("max_stages", "expected_rc", "expected_count"),
    [(12, 0, 12), (13, 3, None)],
    ids=["BOUND-STAGES-12", "BOUND-STAGES-13"],
)
def test_stage_count_boundary(tmp_path, max_stages, expected_rc, expected_count):
    """题面：「`max_stages` 是 2 到 12 的整数」"""
    batch, _ = generate_batch(size=12, runs=2)
    batch["max_stages"] = max_stages
    result, output, _ = run_batch(tmp_path, batch)
    model = load_model(output)
    case_ids = ("BOUND-STAGES-12", "BOUND-STAGES-13")
    assert result.returncode == expected_rc
    assert model.get("stage_count") == expected_count
    assert case_ids == ("BOUND-STAGES-12", "BOUND-STAGES-13")


@pytest.mark.parametrize(
    ("sample_count", "expected_rc"),
    [(32, 0), (33, 3)],
    ids=["BOUND-SAMPLES-32", "BOUND-SAMPLES-33"],
)
def test_service_sample_boundaries(tmp_path, sample_count, expected_rc):
    """题面：「`service_samples` 是 1 到 32 个 1 到 3600000 的整数」"""
    batch, _ = generate_batch(size=5, runs=2)
    batch["runs"][0]["nodes"][0]["service_samples"] = [
        100 + index for index in range(sample_count)
    ]
    result, output, _ = run_batch(tmp_path, batch)
    case_ids = ("BOUND-SAMPLES-32", "BOUND-SAMPLES-33")
    assert result.returncode == expected_rc
    assert output.exists() is (expected_rc == 0)
    assert case_ids == ("BOUND-SAMPLES-32", "BOUND-SAMPLES-33")


@pytest.mark.case_ids("BOUND-RUNS-6")
def test_run_count_boundary(tmp_path):
    """题面：「`runs` 是 2 到 5 项的非空数组」"""
    batch, _ = generate_batch(size=5, runs=6)
    result, output, _ = run_batch(tmp_path, batch)
    case_id = "BOUND-RUNS-6"
    assert result.returncode == 3
    assert result.stderr == "route model refused: " + "invalid_schema\n"
    assert output.exists() is False
    assert case_id == "BOUND-RUNS-6"


@pytest.mark.case_ids("CROSS-EDGE", "EDGE-POSITIVE")
def test_edge_count_must_match_visit_balance(tmp_path):
    """题面：「对每个节点，所有后继边的计数与 `exit_count` 之和等于 `visits`。」"""
    batch, _ = generate_batch(size=5, runs=2)
    batch["runs"][0]["nodes"][0]["visits"] += 1
    result, output, _ = run_batch(tmp_path, batch)
    case_ids = ("CROSS-EDGE", "EDGE-POSITIVE")
    assert result.returncode == 3
    assert result.stderr == "route model refused: " + "invalid_schema\n"
    assert output.exists() is False
    assert case_ids == ("CROSS-EDGE", "EDGE-POSITIVE")


def test_parse_failure_is_quiet_and_traceback_free(tmp_path):
    """题面：「任何失败路径都不得打印 traceback、改写输入文件或发布半成品。」"""
    input_path = tmp_path / "batch.json"
    output_path = tmp_path / "model.json"
    input_path.write_bytes(b'{"schema_version":"route-replay/1"')
    result = run_candidate(input_path, output_path)
    assert result.returncode == 3
    assert result.stdout == ""
    assert result.stderr == "route model refused: " + "invalid_json\n"
    assert "traceback" not in result.stderr.lower()


@pytest.mark.case_ids("FAIL-SCHEMA", "EXIT-CODE", "STDERR-CONTRACT")
def test_schema_refusal_uses_exact_stderr_prefix(tmp_path):
    """题面：「标准错误恰好写一行，前缀逐字为 `route model refused: `」"""
    batch, _ = generate_batch(size=5, runs=2)
    del batch["batch_id"]
    result, output, _ = run_batch(tmp_path, batch)
    case_ids = (
        "FAIL-SCHEMA",
        "EXIT-CODE",
        "STDERR-CONTRACT",
    )
    assert result.returncode == 3
    assert result.stdout == ""
    assert result.stderr == "route model refused: " + "invalid_schema\n"
    assert output.exists() is False
    assert case_ids == (
        "FAIL-SCHEMA",
        "EXIT-CODE",
        "STDERR-CONTRACT",
    )


@pytest.mark.case_ids("FAIL-MODEL", "CANON-ROLE")
def test_unsupported_model_is_refused(tmp_path):
    """题面：「若两个阶段具有相同根化角色，或不同回放无法在保持服务类别和非零边关系的前提下对齐，这组数据不能形成可交付模型。」"""
    result, output, _ = run_batch(tmp_path, symmetric_batch())
    case_ids = ("FAIL-MODEL", "CANON-ROLE")
    assert result.returncode == 3
    assert result.stderr == "route model refused: " + "unsupported_model\n"
    assert output.exists() is False
    assert case_ids == ("FAIL-MODEL", "CANON-ROLE")


@pytest.mark.case_ids("PUBLISH-ATOMIC", "PUBLISH-CLEANUP", "FAILURE-CODES")
def test_publish_failure_preserves_bytes_mode_and_cleanliness(tmp_path):
    """题面：「已经存在的旧文件在失败时保持原字节和原权限，目录中不能留下临时文件。」"""
    batch, _ = generate_batch(size=5, runs=2)
    input_path = tmp_path / "batch.json"
    publish_dir = tmp_path / "publish"
    output_path = publish_dir / "model.json"
    write_json(input_path, batch)
    tmp_path.chmod(0o755)
    input_path.chmod(0o644)
    publish_dir.mkdir()
    old_bytes = b"previous-model\n"
    output_path.write_bytes(old_bytes)
    output_path.chmod(0o640)
    publish_dir.chmod(0o555)
    result = run_candidate(input_path, output_path, drop_privileges=True)
    publish_dir.chmod(0o755)
    case_ids = ("PUBLISH-ATOMIC", "PUBLISH-CLEANUP", "FAILURE-CODES")
    assert result.returncode == 3
    assert result.stdout == ""
    assert result.stderr == "route model refused: " + "output_failed\n"
    assert output_path.read_bytes() == old_bytes
    assert file_mode(output_path) == 0o640
    assert list(publish_dir.glob(".route-model-*")) == []
    assert case_ids == (
        "PUBLISH-ATOMIC",
        "PUBLISH-CLEANUP",
        "FAILURE-CODES",
    )


def test_full_cross_run_recovery_combination(tmp_path):
    """题面：「公开上限为 12 个阶段、5 次回放」"""
    batch, truth = generate_batch(
        size=12,
        runs=5,
        topology_seed=12005,
        flow_seed=22007,
        id_seed=33009,
    )
    result, output, _ = run_batch(tmp_path, batch)
    model = load_model(output)
    assert result.returncode == 0
    assert canonicalized_model(model) == expected_model(batch, truth)
    assert model["stage_count"] == 12
    assert model["calibration"]["aligned_runs"] == 5


@pytest.mark.case_ids("BOUND-RUNS-5")
def test_upper_bound_batch_completes_within_public_limit(tmp_path):
    """题面：「达到这个规模的一批数据需要在 40 秒内完成」"""
    batch, _ = generate_batch(
        size=12,
        runs=5,
        topology_seed=14503,
        flow_seed=24509,
        id_seed=34511,
    )
    input_path = tmp_path / "batch.json"
    output_path = tmp_path / "model.json"
    case_id = "BOUND-RUNS-5"
    write_json(input_path, batch)
    started = time.monotonic()
    result = run_candidate(input_path, output_path, timeout=45)
    elapsed = time.monotonic() - started
    model = load_model(output_path)
    assert result.returncode == 0
    assert elapsed < 40
    assert model["stage_count"] == 12
    assert model["calibration"]["aligned_runs"] == 5
    assert case_id == "BOUND-RUNS-5"


@pytest.mark.case_ids(
    "OUTPUT-TOP",
    "OUTPUT-TOP-GROUPS",
    "OUTPUT-SCHEMA",
    "OUTPUT-STAGE-KEYS",
    "OUTPUT-DIST-KEYS",
    "OUTPUT-EDGE-KEYS",
    "OUTPUT-CAL-KEYS",
    "OUTPUT-VISIT-KEYS",
    "OUTPUT-STAGE-TYPES",
    "OUTPUT-STAGE-IDENTITY-KEYS",
    "OUTPUT-STAGE-SUPPORT-KEYS",
    "OUTPUT-ZERO-EDGES",
    "OUTPUT-ALIGNED-RUNS",
    "OUTPUT-CAL-PROJ-KEYS",
    "OUTPUT-CAL-SUPPORT-KEYS",
    "OUTPUT-VISIT-ORDER",
)
def test_candidate_produces_complete_model(tmp_path):
    """题面：「把多次回放合成一份可重复的阶段表和吸收链模型。」"""
    batch, _ = generate_batch(size=6, runs=3)
    result, output, _ = run_batch(tmp_path, batch)
    model = load_model(output)
    case_ids = (
        "OUTPUT-TOP",
        "OUTPUT-TOP-GROUPS",
        "OUTPUT-SCHEMA",
        "OUTPUT-STAGE-KEYS",
        "OUTPUT-DIST-KEYS",
        "OUTPUT-EDGE-KEYS",
        "OUTPUT-CAL-KEYS",
        "OUTPUT-VISIT-KEYS",
        "OUTPUT-STAGE-TYPES",
        "OUTPUT-STAGE-IDENTITY-KEYS",
        "OUTPUT-STAGE-SUPPORT-KEYS",
        "OUTPUT-ZERO-EDGES",
        "OUTPUT-ALIGNED-RUNS",
        "OUTPUT-CAL-PROJ-KEYS",
        "OUTPUT-CAL-SUPPORT-KEYS",
        "OUTPUT-VISIT-ORDER",
    )
    assert result.returncode == 0
    assert set(model) == {
        "schema_version",
        "batch_id",
        "stage_count",
        "stages",
        "entries",
        "transitions",
        "exits",
        "calibration",
    }
    assert model["stage_count"] >= 2
    assert model["calibration"]["aligned_runs"] == 3
    assert model["schema_version"] == "route-model/1"
    assert {
        frozenset(item) for item in model["stages"]
    } == {frozenset({"id", "service_class", "median_service_ms", "visits"})}
    assert {
        frozenset(item) for item in model["entries"]
    } == {frozenset({"stage", "probability"})}
    assert {
        frozenset(item) for item in model["exits"]
    } == {frozenset({"stage", "probability"})}
    assert {
        frozenset(item) for item in model["transitions"]
    } == {frozenset({"from", "to", "probability"})}
    assert all(item["probability"] > 0 for item in model["transitions"])
    assert set(model["calibration"]) == {
        "aligned_runs",
        "expected_cycle_service_ms",
        "expected_visits",
        "support_ratio",
    }
    assert {
        frozenset(item) for item in model["calibration"]["expected_visits"]
    } == {frozenset({"stage", "expectation"})}
    assert all(
        type(item["service_class"]) is int
        and type(item["median_service_ms"]) is int
        for item in model["stages"]
    )
    assert all(item["probability"] > 0 for item in model["transitions"])
    assert model["calibration"]["aligned_runs"] == len(batch["runs"])
    assert [
        item["stage"] for item in model["calibration"]["expected_visits"]
    ] == [item["id"] for item in model["stages"]]
    assert case_ids == (
        "OUTPUT-TOP",
        "OUTPUT-TOP-GROUPS",
        "OUTPUT-SCHEMA",
        "OUTPUT-STAGE-KEYS",
        "OUTPUT-DIST-KEYS",
        "OUTPUT-EDGE-KEYS",
        "OUTPUT-CAL-KEYS",
        "OUTPUT-VISIT-KEYS",
        "OUTPUT-STAGE-TYPES",
        "OUTPUT-STAGE-IDENTITY-KEYS",
        "OUTPUT-STAGE-SUPPORT-KEYS",
        "OUTPUT-ZERO-EDGES",
        "OUTPUT-ALIGNED-RUNS",
        "OUTPUT-CAL-PROJ-KEYS",
        "OUTPUT-CAL-SUPPORT-KEYS",
        "OUTPUT-VISIT-ORDER",
    )
