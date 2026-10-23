#!/usr/bin/env python3
"""Summarize route replay batches into a local stage table."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def median(values: list[int]) -> int:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return int(round((ordered[middle - 1] + ordered[middle]) / 2))


def summarize(payload: dict[str, Any]) -> dict[str, Any]:
    grouped: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"visits": 0, "samples": [], "entries": 0, "exits": 0}
    )
    edges: Counter[tuple[str, str]] = Counter()

    for run in payload["runs"]:
        nodes = {node["local_id"]: node for node in run["nodes"]}
        for local_id, node in nodes.items():
            item = grouped[local_id]
            item["visits"] += node["visits"]
            item["samples"].extend(node["service_samples"])
            item["entries"] += node["entry_count"]
            item["exits"] += node["exit_count"]
            item["service_class"] = node["service_class"]
        for edge in run["edges"]:
            edges[(edge["from"], edge["to"])] += edge["count"]

    ids = sorted(grouped)
    stages = [
        {
            "id": f"S{index:02d}",
            "service_class": grouped[local_id].get("service_class", 0),
            "median_service_ms": median(grouped[local_id]["samples"]),
            "visits": grouped[local_id]["visits"],
        }
        for index, local_id in enumerate(ids, start=1)
    ]
    id_for_local = {
        local_id: f"S{index:02d}" for index, local_id in enumerate(ids, start=1)
    }
    transitions = [
        {
            "from": id_for_local[left],
            "to": id_for_local[right],
            "probability": round(count / grouped[left]["visits"], 6),
        }
        for (left, right), count in sorted(edges.items())
    ]
    return {
        "schema_version": "route-model/1",
        "batch_id": payload["batch_id"],
        "stage_count": len(stages),
        "stages": stages,
        "entries": [],
        "transitions": transitions,
        "exits": [],
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".route-model-",
        dir=path.parent,
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
    os.replace(temporary, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_path")
    parser.add_argument("output_path")
    args = parser.parse_args(argv)
    try:
        payload = load_json(Path(args.input_path))
        write_json(Path(args.output_path), summarize(payload))
    except Exception:
        print("route model refused: invalid_schema", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
