#!/usr/bin/env python3
"""Recover a canonical multi-run workflow model from anonymous local labels."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any


TOP_KEYS = {"schema_version", "batch_id", "max_stages", "runs"}
RUN_KEYS = {"source_id", "nodes", "edges"}
NODE_KEYS = {
    "local_id",
    "service_class",
    "visits",
    "entry_count",
    "exit_count",
    "service_samples",
}
EDGE_KEYS = {"from", "to", "count"}
BATCH_RE = re.compile(r"BAT-[A-F0-9]{8}\Z")
SOURCE_RE = re.compile(r"SRC-[A-Z0-9]{8}\Z")
LOCAL_RE = re.compile(r"N-[0-9a-f]{8}\Z")
SIX = Decimal("0.000001")


class Refusal(Exception):
    """A stable public refusal category."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class DuplicateKey(ValueError):
    """Raised when one JSON object repeats a key."""


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKey(key)
        result[key] = value
    return result


def is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def require_int(value: Any, low: int, high: int) -> int:
    if not is_int(value) or not low <= value <= high:
        raise Refusal("invalid_schema")
    return value


def require_exact_keys(value: dict[str, Any], expected: set[str]) -> None:
    if set(value) != expected:
        raise Refusal("invalid_schema")


def load_payload(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise Refusal("input_unreadable") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Refusal("invalid_json") from exc
    try:
        payload = json.loads(text, object_pairs_hook=unique_object)
    except DuplicateKey as exc:
        raise Refusal("invalid_schema") from exc
    except json.JSONDecodeError as exc:
        raise Refusal("invalid_json") from exc
    if not isinstance(payload, dict):
        raise Refusal("invalid_schema")
    return payload


def validate(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], int, str]:
    require_exact_keys(payload, TOP_KEYS)
    if payload["schema_version"] != "route-replay/1":
        raise Refusal("invalid_schema")
    batch_id = payload["batch_id"]
    if not isinstance(batch_id, str) or BATCH_RE.fullmatch(batch_id) is None:
        raise Refusal("invalid_schema")
    max_stages = require_int(payload["max_stages"], 2, 12)
    runs = payload["runs"]
    if not isinstance(runs, list) or not 2 <= len(runs) <= 5:
        raise Refusal("invalid_schema")

    node_count: int | None = None
    source_ids: set[str] = set()
    total_entries = 0
    for run in runs:
        if not isinstance(run, dict):
            raise Refusal("invalid_schema")
        require_exact_keys(run, RUN_KEYS)
        source_id = run["source_id"]
        if not isinstance(source_id, str) or SOURCE_RE.fullmatch(source_id) is None:
            raise Refusal("invalid_schema")
        if source_id in source_ids:
            raise Refusal("invalid_schema")
        source_ids.add(source_id)
        nodes = run["nodes"]
        edges = run["edges"]
        if not isinstance(nodes, list) or not isinstance(edges, list):
            raise Refusal("invalid_schema")
        if not 2 <= len(nodes) <= max_stages:
            raise Refusal("invalid_schema")
        if node_count is None:
            node_count = len(nodes)
        elif node_count != len(nodes):
            raise Refusal("invalid_schema")

        ids: set[str] = set()
        outgoing: Counter[str] = Counter()
        for node in nodes:
            if not isinstance(node, dict):
                raise Refusal("invalid_schema")
            require_exact_keys(node, NODE_KEYS)
            local_id = node["local_id"]
            if not isinstance(local_id, str) or LOCAL_RE.fullmatch(local_id) is None:
                raise Refusal("invalid_schema")
            if local_id in ids:
                raise Refusal("invalid_schema")
            ids.add(local_id)
            require_int(node["service_class"], 0, 3)
            visits = require_int(node["visits"], 1, 100_000)
            entry_count = require_int(node["entry_count"], 0, visits)
            require_int(node["exit_count"], 0, visits)
            total_entries += entry_count
            samples = node["service_samples"]
            if not isinstance(samples, list) or not 1 <= len(samples) <= 32:
                raise Refusal("invalid_schema")
            for sample in samples:
                require_int(sample, 1, 3_600_000)

        edge_pairs: set[tuple[str, str]] = set()
        for edge in edges:
            if not isinstance(edge, dict):
                raise Refusal("invalid_schema")
            require_exact_keys(edge, EDGE_KEYS)
            left = edge["from"]
            right = edge["to"]
            if left not in ids or right not in ids:
                raise Refusal("invalid_schema")
            if (left, right) in edge_pairs:
                raise Refusal("invalid_schema")
            edge_pairs.add((left, right))
            count = require_int(edge["count"], 1, 1_000_000_000)
            outgoing[left] += count

        by_id = {node["local_id"]: node for node in nodes}
        for local_id, node in by_id.items():
            if outgoing[local_id] + node["exit_count"] != node["visits"]:
                raise Refusal("invalid_schema")

    if total_entries == 0:
        raise Refusal("invalid_schema")
    return runs, max_stages, batch_id


def color_payload(
    index: int,
    colors: list[str],
    adjacency: list[list[bool]],
) -> str:
    outgoing = sorted(
        colors[target]
        for target, present in enumerate(adjacency[index])
        if present
    )
    incoming = sorted(
        colors[source]
        for source, row in enumerate(adjacency)
        if row[index]
    )
    return (
        colors[index]
        + "|O:"
        + ",".join(outgoing)
        + "|I:"
        + ",".join(incoming)
    )


def rooted_codes(run: dict[str, Any]) -> dict[str, str]:
    nodes = run["nodes"]
    size = len(nodes)
    local_ids = [node["local_id"] for node in nodes]
    index_for_id = {local_id: index for index, local_id in enumerate(local_ids)}
    classes = [node["service_class"] for node in nodes]
    adjacency = [[False for _ in range(size)] for _ in range(size)]
    for edge in run["edges"]:
        adjacency[index_for_id[edge["from"]]][index_for_id[edge["to"]]] = True

    codes: dict[str, str] = {}
    for root in range(size):
        colors = [
            f"{'R' if index == root else 'C'}{classes[index]}"
            for index in range(size)
        ]
        for _ in range(size + 1):
            colors = [
                hashlib.sha256(
                    color_payload(index, colors, adjacency).encode("utf-8")
                ).hexdigest()
                for index in range(size)
            ]
        if len(set(colors)) != size:
            raise Refusal("unsupported_model")
        order = [root] + sorted(
            (index for index in range(size) if index != root),
            key=lambda index: colors[index],
        )
        class_code = "".join(str(classes[index]) for index in order)
        adjacency_code = "".join(
            "1" if adjacency[source][target] else "0"
            for source in order
            for target in order
        )
        codes[local_ids[root]] = (
            f"R{classes[root]}|{class_code}|{adjacency_code}"
        )
    if len(set(codes.values())) != size:
        raise Refusal("unsupported_model")
    return codes


def round_median(values: list[int]) -> int:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    average = Decimal(ordered[middle - 1] + ordered[middle]) / Decimal(2)
    return int(average.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def q6(value: Decimal) -> float:
    return float(value.quantize(SIX, rounding=ROUND_HALF_UP))


def solve_linear(matrix: list[list[Decimal]], vector: list[Decimal]) -> list[Decimal]:
    size = len(vector)
    augmented = [list(matrix[row]) + [vector[row]] for row in range(size)]
    for column in range(size):
        pivot = max(
            range(column, size),
            key=lambda row: abs(augmented[row][column]),
        )
        if augmented[pivot][column] == 0:
            raise Refusal("unsupported_model")
        if pivot != column:
            augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            if factor == 0:
                continue
            augmented[row] = [
                augmented[row][index] - factor * augmented[column][index]
                for index in range(size + 1)
            ]
    return [augmented[row][-1] for row in range(size)]


def align_runs(
    runs: list[dict[str, Any]],
) -> tuple[list[str], list[dict[int, int]], list[str]]:
    run_codes: list[dict[str, str]] = []
    all_codes: set[str] = set()
    for run in runs:
        codes = rooted_codes(run)
        run_codes.append(codes)
        all_codes.update(codes.values())

    if any(set(codes.values()) != all_codes for codes in run_codes):
        raise Refusal("unsupported_model")
    ordered = sorted(all_codes)
    global_for_code = {code: index for index, code in enumerate(ordered)}
    mappings = []
    node_counts = {len(run["nodes"]) for run in runs}
    if len(node_counts) != 1 or len(ordered) != next(iter(node_counts)):
        raise Refusal("unsupported_model")
    for run, codes in zip(runs, run_codes):
        mappings.append(
            {
                index: global_for_code[codes[node["local_id"]]]
                for index, node in enumerate(run["nodes"])
            }
        )
    return ordered, mappings, ["S01"] * len(ordered)


def build_model(
    runs: list[dict[str, Any]], max_stages: int, batch_id: str
) -> dict[str, Any]:
    codes, mappings, _ = align_runs(runs)
    stage_count = len(codes)
    if not 2 <= stage_count <= max_stages:
        raise Refusal("unsupported_model")
    identifiers = [f"S{index + 1:02d}" for index in range(stage_count)]

    visits = [0] * stage_count
    entry_counts = [0] * stage_count
    exit_counts = [0] * stage_count
    samples: list[list[int]] = [[] for _ in range(stage_count)]
    edge_counts: Counter[tuple[int, int]] = Counter()
    service_classes: list[int | None] = [None] * stage_count

    for run, mapping in zip(runs, mappings):
        local_to_index = {
            node["local_id"]: index for index, node in enumerate(run["nodes"])
        }
        for local_index, node in enumerate(run["nodes"]):
            stage_index = mapping[local_index]
            visits[stage_index] += node["visits"]
            entry_counts[stage_index] += node["entry_count"]
            exit_counts[stage_index] += node["exit_count"]
            samples[stage_index].extend(node["service_samples"])
            known_class = service_classes[stage_index]
            if known_class is None:
                service_classes[stage_index] = node["service_class"]
            elif known_class != node["service_class"]:
                raise Refusal("unsupported_model")
        for edge in run["edges"]:
            left = mapping[local_to_index[edge["from"]]]
            right = mapping[local_to_index[edge["to"]]]
            edge_counts[(left, right)] += edge["count"]

    if any(value is None for value in service_classes):
        raise Refusal("unsupported_model")
    medians = [round_median(values) for values in samples]
    leave_counts: Counter[int] = Counter(
        {index: exit_counts[index] for index in range(stage_count)}
    )
    for (left, _), count in edge_counts.items():
        leave_counts[left] += count
    if any(leave_counts[index] == 0 for index in range(stage_count)):
        raise Refusal("unsupported_model")

    entry_total = sum(entry_counts)
    if entry_total == 0:
        raise Refusal("unsupported_model")
    entries = [
        {
            "stage": identifiers[index],
            "probability": q6(Decimal(entry_counts[index]) / Decimal(entry_total)),
        }
        for index in range(stage_count)
    ]
    exits = [
        {
            "stage": identifiers[index],
            "probability": q6(
                Decimal(exit_counts[index]) / Decimal(leave_counts[index])
            ),
        }
        for index in range(stage_count)
    ]
    transitions = [
        {
            "from": identifiers[left],
            "to": identifiers[right],
            "probability": q6(Decimal(count) / Decimal(leave_counts[left])),
        }
        for (left, right), count in sorted(edge_counts.items())
    ]

    matrix = [[Decimal(0) for _ in range(stage_count)] for _ in range(stage_count)]
    for index in range(stage_count):
        matrix[index][index] = Decimal(1)
        for (left, right), count in edge_counts.items():
            if left == index:
                matrix[index][right] -= Decimal(count) / Decimal(leave_counts[left])
    initial = [
        Decimal(entry_counts[index]) / Decimal(entry_total)
        for index in range(stage_count)
    ]
    expected = solve_linear(matrix, initial)
    if any(value < 0 for value in expected):
        raise Refusal("unsupported_model")
    cycle = sum(
        expected[index] * Decimal(medians[index])
        for index in range(stage_count)
    )
    total_visits = sum(visits)

    return {
        "schema_version": "route-model/1",
        "batch_id": batch_id,
        "stage_count": stage_count,
        "stages": [
            {
                "id": identifiers[index],
                "service_class": service_classes[index],
                "median_service_ms": medians[index],
                "visits": visits[index],
            }
            for index in range(stage_count)
        ],
        "entries": entries,
        "transitions": transitions,
        "exits": exits,
        "calibration": {
            "aligned_runs": len(runs),
            "expected_cycle_service_ms": q6(cycle),
            "expected_visits": [
                {
                    "stage": identifiers[index],
                    "expectation": q6(expected[index]),
                }
                for index in range(stage_count)
            ],
            "support_ratio": q6(
                Decimal(total_visits) / Decimal(total_visits)
            ),
        },
    }


def write_model(path: Path, model: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=".route-model-",
            dir=path.parent,
        )
        payload = (
            json.dumps(
                model,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o644)
            os.replace(temporary, path)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
    except OSError as exc:
        raise Refusal("output_failed") from exc


def run(input_path: Path, output_path: Path) -> None:
    payload = load_payload(input_path)
    runs, max_stages, batch_id = validate(payload)
    model = build_model(runs, max_stages, batch_id)
    write_model(output_path, model)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_path")
    parser.add_argument("output_path")
    args = parser.parse_args(argv)
    try:
        run(Path(args.input_path), Path(args.output_path))
    except Refusal as exc:
        print(f"route model refused: {exc.code}", file=sys.stderr)
        return 3
    except Exception:
        print("route model refused: invalid_schema", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
