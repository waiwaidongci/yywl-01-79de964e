from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import random
import stat
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any


SIX = Decimal("0.000001")
ENTRY = os.environ.get("YEL004_ENTRY", "/app/route_model.py")
SERVICE_BASES = [83, 117, 149, 181, 223, 257, 293, 331, 373, 419, 463, 509]
SERVICE_PATTERN = [0, 2, 1, 3, 2, 0, 1, 3, 3, 0, 2, 1]


@dataclass(frozen=True)
class CandidateResult:
    returncode: int
    stdout: str
    stderr: str
    stdout_bytes: bytes
    stderr_bytes: bytes


def q6(value: Decimal) -> float:
    return float(value.quantize(SIX, rounding=ROUND_HALF_UP))


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


def codes_for_support(
    size: int,
    support: set[tuple[int, int]],
    classes: list[int],
) -> list[str]:
    adjacency = [[False for _ in range(size)] for _ in range(size)]
    for left, right in support:
        adjacency[left][right] = True
    codes = []
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
            codes.append("")
            continue
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
        codes.append(f"R{classes[root]}|{class_code}|{adjacency_code}")
    return codes


def unique_support(size: int, seed: int) -> tuple[set[tuple[int, int]], list[str]]:
    classes = SERVICE_PATTERN[:size]
    attempt = seed
    while True:
        rng = random.Random(attempt)
        support = {
            (index, (index + 1) % size)
            for index in range(size)
        }
        support.update(
            (index, (index + 3) % size)
            for index in range(size)
        )
        for left in range(size):
            for right in range(size):
                if left != right and rng.random() < 0.24:
                    support.add((left, right))
        codes = codes_for_support(size, support, classes)
        if "" not in codes and len(set(codes)) == size:
            return support, codes
        attempt += 1


def generate_batch(
    size: int = 6,
    runs: int = 3,
    topology_seed: int = 2104,
    flow_seed: int = 907,
    id_seed: int = 3301,
) -> tuple[dict[str, Any], dict[str, Any]]:
    support, codes = unique_support(size, topology_seed)
    classes = SERVICE_PATTERN[:size]
    rank = {
        canonical_index: rank_index
        for rank_index, canonical_index in enumerate(
            sorted(range(size), key=lambda index: codes[index])
        )
    }
    mapping_by_run: list[dict[str, int]] = []
    run_payloads = []
    for run_index in range(runs):
        flow_rng = random.Random(flow_seed + run_index * 101)
        id_rng = random.Random(id_seed + run_index * 503)
        order = list(range(size))
        id_rng.shuffle(order)
        local_for_node = {
            node: f"N-{id_rng.getrandbits(32):08x}" for node in range(size)
        }
        if len(set(local_for_node.values())) != size:
            local_for_node = {
                node: f"N-{(node * 2654435761 + id_seed + run_index):08x}"
                for node in range(size)
            }
        edge_counts = {
            edge: flow_rng.randint(3, 37) for edge in sorted(support)
        }
        node_parameters = {}
        for canonical_index in range(size):
            outgoing = sum(
                count
                for (left, _), count in edge_counts.items()
                if left == canonical_index
            )
            exit_count = flow_rng.randint(2, 17)
            visits = outgoing + exit_count
            entry_count = flow_rng.randint(0, visits)
            base = SERVICE_BASES[canonical_index]
            sample_count = 3 + flow_rng.randrange(5)
            samples = [
                max(1, base + flow_rng.randint(-11, 11))
                for _ in range(sample_count)
            ]
            node_parameters[canonical_index] = {
                "visits": visits,
                "entry_count": entry_count,
                "exit_count": exit_count,
                "service_samples": samples,
            }
        nodes = []
        mappings: dict[str, int] = {}
        for canonical_index in order:
            local_id = local_for_node[canonical_index]
            parameters = node_parameters[canonical_index]
            nodes.append(
                {
                    "local_id": local_id,
                    "service_class": classes[canonical_index],
                    "visits": parameters["visits"],
                    "entry_count": parameters["entry_count"],
                    "exit_count": parameters["exit_count"],
                    "service_samples": parameters["service_samples"],
                }
            )
            mappings[local_id] = rank[canonical_index]
        edges = [
            {
                "from": local_for_node[left],
                "to": local_for_node[right],
                "count": count,
            }
            for (left, right), count in sorted(edge_counts.items())
        ]
        entry_counts = [node["entry_count"] for node in nodes]
        if sum(entry_counts) == 0:
            nodes[0]["entry_count"] = 1
        run_payloads.append(
            {
                "source_id": f"SRC-{flow_seed + run_index:08X}"[-12:],
                "nodes": nodes,
                "edges": edges,
            }
        )
        mapping_by_run.append(mappings)

    used_sources: set[str] = set()
    for index, run in enumerate(run_payloads):
        candidate = f"SRC-{id_seed + index:08X}"[-12:]
        while candidate in used_sources:
            index += 1
            candidate = f"SRC-{id_seed + index:08X}"[-12:]
        used_sources.add(candidate)
        run["source_id"] = candidate

    payload = {
        "schema_version": "route-replay/1",
        "batch_id": f"BAT-{(topology_seed ^ flow_seed) & 0xFFFFFFFF:08X}",
        "max_stages": max(2, size),
        "runs": run_payloads,
    }
    truth = {
        "size": size,
        "classes": classes,
        "classes_by_rank": [
            classes[canonical_index]
            for canonical_index in sorted(range(size), key=lambda index: codes[index])
        ],
        "codes": codes,
        "rank": rank,
        "mappings": mapping_by_run,
        "topology_seed": topology_seed,
        "flow_seed": flow_seed,
        "id_seed": id_seed,
    }
    return payload, truth


def solve_linear(matrix: list[list[Decimal]], vector: list[Decimal]) -> list[Decimal]:
    size = len(vector)
    augmented = [list(matrix[row]) + [vector[row]] for row in range(size)]
    for column in range(size):
        pivot = max(
            range(column, size),
            key=lambda row: abs(augmented[row][column]),
        )
        if augmented[pivot][column] == 0:
            raise ValueError("singular")
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


def round_median(values: list[int]) -> int:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    average = Decimal(ordered[middle - 1] + ordered[middle]) / Decimal(2)
    return int(average.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def expected_model(payload: dict[str, Any], truth: dict[str, Any]) -> dict[str, Any]:
    size = truth["size"]
    visits = [0] * size
    entries = [0] * size
    exits = [0] * size
    samples: list[list[int]] = [[] for _ in range(size)]
    edges: Counter[tuple[int, int]] = Counter()
    for run, mapping in zip(payload["runs"], truth["mappings"]):
        for node in run["nodes"]:
            stage = mapping[node["local_id"]]
            visits[stage] += node["visits"]
            entries[stage] += node["entry_count"]
            exits[stage] += node["exit_count"]
            samples[stage].extend(node["service_samples"])
        for edge in run["edges"]:
            left = mapping[edge["from"]]
            right = mapping[edge["to"]]
            edges[(left, right)] += edge["count"]
    medians = [round_median(values) for values in samples]
    leave = list(exits)
    for (left, _), count in edges.items():
        leave[left] += count
    entry_total = sum(entries)
    matrix = [[Decimal(0) for _ in range(size)] for _ in range(size)]
    for left in range(size):
        matrix[left][left] = Decimal(1)
        for (source, target), count in edges.items():
            if source == left:
                matrix[left][target] -= Decimal(count) / Decimal(leave[left])
    initial = [Decimal(entries[index]) / Decimal(entry_total) for index in range(size)]
    expected = solve_linear(matrix, initial)
    cycle = sum(
        expected[index] * Decimal(medians[index])
        for index in range(size)
    )
    ids = [f"S{index + 1:02d}" for index in range(size)]
    return {
        "schema_version": "route-model/1",
        "batch_id": payload["batch_id"],
        "stage_count": size,
        "stages": [
            {
                "id": ids[index],
                "service_class": truth["classes_by_rank"][index],
                "median_service_ms": medians[index],
                "visits": visits[index],
            }
            for index in range(size)
        ],
        "entries": [
            {
                "stage": ids[index],
                "probability": q6(Decimal(entries[index]) / Decimal(entry_total)),
            }
            for index in range(size)
        ],
        "transitions": [
            {
                "from": ids[left],
                "to": ids[right],
                "probability": q6(Decimal(count) / Decimal(leave[left])),
            }
            for (left, right), count in sorted(edges.items())
        ],
        "exits": [
            {
                "stage": ids[index],
                "probability": q6(Decimal(exits[index]) / Decimal(leave[index])),
            }
            for index in range(size)
        ],
        "calibration": {
            "aligned_runs": len(payload["runs"]),
            "expected_cycle_service_ms": q6(cycle),
            "expected_visits": [
                {
                    "stage": ids[index],
                    "expectation": q6(expected[index]),
                }
                for index in range(size)
            ],
            "support_ratio": 1.0,
        },
    }


def canonicalized_model(model: dict[str, Any]) -> dict[str, Any]:
    if "calibration" not in model:
        return model
    value = copy.deepcopy(model)
    stage_ids = sorted(item["id"] for item in value["stages"])
    index_for_id = {stage_id: index for index, stage_id in enumerate(stage_ids)}
    classes = [
        next(
            item["service_class"]
            for item in value["stages"]
            if item["id"] == stage_id
        )
        for stage_id in stage_ids
    ]
    support = {
        (index_for_id[edge["from"]], index_for_id[edge["to"]])
        for edge in value["transitions"]
    }
    codes = codes_for_support(len(stage_ids), support, classes)
    ranks = {
        stage_id: rank
        for rank, canonical_index in enumerate(
            sorted(range(len(stage_ids)), key=lambda index: codes[index])
        )
        for stage_id in [stage_ids[canonical_index]]
    }
    identifier = {
        stage_id: f"S{ranks[stage_id] + 1:02d}" for stage_id in stage_ids
    }
    stages = sorted(
        (
            {**item, "id": identifier[item["id"]]}
            for item in value["stages"]
        ),
        key=lambda item: item["id"],
    )
    entries = sorted(
        (
            {**item, "stage": identifier[item["stage"]]}
            for item in value["entries"]
        ),
        key=lambda item: item["stage"],
    )
    exits = sorted(
        (
            {**item, "stage": identifier[item["stage"]]}
            for item in value["exits"]
        ),
        key=lambda item: item["stage"],
    )
    transitions = sorted(
        (
            {
                **item,
                "from": identifier[item["from"]],
                "to": identifier[item["to"]],
            }
            for item in value["transitions"]
        ),
        key=lambda item: (item["from"], item["to"]),
    )
    expected_visits = sorted(
        (
            {**item, "stage": identifier[item["stage"]]}
            for item in value["calibration"]["expected_visits"]
        ),
        key=lambda item: item["stage"],
    )
    value["stages"] = stages
    value["entries"] = entries
    value["exits"] = exits
    value["transitions"] = transitions
    value["calibration"]["expected_visits"] = expected_visits
    return value


def model_is_canonical(model: dict[str, Any]) -> bool:
    stage_ids = [item["id"] for item in model["stages"]]
    index_for_id = {stage_id: index for index, stage_id in enumerate(stage_ids)}
    classes = [item["service_class"] for item in model["stages"]]
    support = {
        (index_for_id[edge["from"]], index_for_id[edge["to"]])
        for edge in model["transitions"]
    }
    codes = codes_for_support(len(stage_ids), support, classes)
    canonical_order = [
        stage_ids[index]
        for index in sorted(range(len(stage_ids)), key=lambda item: codes[item])
    ]
    return stage_ids == canonical_order


def force_rank_median_tie(
    payload: dict[str, Any],
    truth: dict[str, Any],
    rank: int,
    lower: int,
    upper: int,
) -> None:
    patterns = [[lower], [upper], [lower, upper], [lower], [upper]]
    for run, mapping, values in zip(payload["runs"], truth["mappings"], patterns):
        local_id = next(
            node_id for node_id, node_rank in mapping.items() if node_rank == rank
        )
        node = next(
            item for item in run["nodes"] if item["local_id"] == local_id
        )
        node["service_samples"] = values


def write_json(path: Path, payload: dict[str, Any]) -> bytes:
    raw = (
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    path.write_bytes(raw)
    path.chmod(0o644)
    return raw


def run_candidate(
    input_path: Path,
    output_path: Path,
    timeout: int = 45,
    drop_privileges: bool = False,
) -> CandidateResult:
    kwargs: dict[str, Any] = {}
    if drop_privileges and os.geteuid() == 0:
        def drop() -> None:
            os.setgid(65534)
            os.setuid(65534)

        kwargs["preexec_fn"] = drop
    completed = subprocess.run(
        [sys.executable, ENTRY, str(input_path), str(output_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
        **kwargs,
    )
    return CandidateResult(
        returncode=completed.returncode,
        stdout=completed.stdout.decode("utf-8", errors="replace"),
        stderr=completed.stderr.decode("utf-8", errors="replace"),
        stdout_bytes=completed.stdout,
        stderr_bytes=completed.stderr,
    )


def run_batch(
    tmp_path: Path,
    payload: dict[str, Any],
    output_name: str = "model.json",
) -> tuple[CandidateResult, Path, bytes]:
    input_path = tmp_path / "batch.json"
    output_path = tmp_path / output_name
    input_raw = write_json(input_path, payload)
    result = run_candidate(input_path, output_path)
    return result, output_path, input_raw


def bad_case(batch: dict[str, Any], case: str) -> tuple[dict[str, Any] | str, str]:
    value = copy.deepcopy(batch)
    if case == "SCHEMA-TOP-MISSING":
        del value["batch_id"]
        return value, "invalid_schema"
    if case == "SCHEMA-TOP-EXTRA":
        value["extra"] = 1
        return value, "invalid_schema"
    if case == "SCHEMA-RUN-MISSING":
        del value["runs"][0]["edges"]
        return value, "invalid_schema"
    if case == "SCHEMA-RUN-EXTRA":
        value["runs"][0]["extra"] = 1
        return value, "invalid_schema"
    if case == "SCHEMA-NODE-MISSING":
        del value["runs"][0]["nodes"][0]["service_samples"]
        return value, "invalid_schema"
    if case == "SCHEMA-NODE-EXTRA":
        value["runs"][0]["nodes"][0]["extra"] = 1
        return value, "invalid_schema"
    if case == "SCHEMA-EDGE-MISSING":
        del value["runs"][0]["edges"][0]["count"]
        return value, "invalid_schema"
    if case == "SCHEMA-EDGE-EXTRA":
        value["runs"][0]["edges"][0]["extra"] = 1
        return value, "invalid_schema"
    if case == "SCHEMA-TYPE-ENUM":
        value["runs"][0]["nodes"][0]["visits"] = True
        value["runs"][0]["nodes"][0]["service_class"] = 4
        return value, "invalid_schema"
    text = json.dumps(value, ensure_ascii=True)
    marker = '"service_class": 0'
    duplicate = '"service_class": 0,"service_class": 0'
    return text.replace(marker, duplicate, 1), "invalid_schema"


def write_case_value(path: Path, value: dict[str, Any] | str) -> None:
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        write_json(path, value)


def break_support(payload: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(payload)
    run = value["runs"][0]
    edge = run["edges"][0]
    existing = {(item["from"], item["to"]) for item in run["edges"]}
    run["edges"].remove(edge)
    candidates = [
        (edge["from"], node["local_id"])
        for node in run["nodes"]
        if (edge["from"], node["local_id"]) not in existing
    ]
    target = candidates[0][1]
    run["edges"].append(
        {"from": edge["from"], "to": target, "count": edge["count"]}
    )
    return value


def symmetric_batch() -> dict[str, Any]:
    nodes = []
    for index in range(2):
        nodes.append(
            {
                "local_id": f"N-{10000000 + index:08x}",
                "service_class": 0,
                "visits": 6,
                "entry_count": 1 if index == 0 else 0,
                "exit_count": 1,
                "service_samples": [100 + index],
            }
        )
    run = {
        "source_id": "SRC-SYMM0001",
        "nodes": nodes,
        "edges": [
            {"from": "N-00989680", "to": "N-00989681", "count": 5},
            {"from": "N-00989681", "to": "N-00989680", "count": 5},
        ],
    }
    second = copy.deepcopy(run)
    second["source_id"] = "SRC-SYMM0002"
    second["nodes"][0]["local_id"] = "N-00000001"
    second["nodes"][1]["local_id"] = "N-00000002"
    second["edges"] = [
        {"from": "N-00000001", "to": "N-00000002", "count": 5},
        {"from": "N-00000002", "to": "N-00000001", "count": 5},
    ]
    return {
        "schema_version": "route-replay/1",
        "batch_id": "BAT-AAAABBBB",
        "max_stages": 2,
        "runs": [run, second],
    }


def load_model(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_bytes())


def file_mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def route_rows(model: dict[str, Any]) -> dict[str, Decimal]:
    rows = {
        item["stage"]: Decimal(str(item["probability"]))
        for item in model["exits"]
    }
    for edge in model["transitions"]:
        rows[edge["from"]] += Decimal(str(edge["probability"]))
    return rows


def route_row_error(model: dict[str, Any]) -> Decimal:
    return max(abs(value - Decimal(1)) for value in route_rows(model).values())
