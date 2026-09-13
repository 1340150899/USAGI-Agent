"""Render generic JSONL spans as parent/child trees without semantic mapping."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def load(path: Path) -> list[dict]:
    files = sorted(path.rglob("*.jsonl")) if path.is_dir() else [path]
    records = []
    for source in files:
        with source.open(encoding="utf-8") as stream:
            for number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise SystemExit(
                        f"{source}:{number}: invalid span JSON: {exc}"
                    ) from exc
                if not isinstance(record, dict):
                    raise SystemExit(f"{source}:{number}: span must be a JSON object")
                records.append(record)
    return records


def render_trace(spans: list[dict], *, show_ids: bool = False) -> list[str]:
    children: dict[str | None, list[dict]] = defaultdict(list)
    ids = {span.get("span_id") for span in spans if span.get("span_id") is not None}
    for span in spans:
        parent = span.get("parent_span_id")
        children[parent if parent in ids else None].append(span)
    for values in children.values():
        values.sort(key=lambda item: item.get("start_time", ""))
    lines = []

    def visit(span: dict, prefix: str, is_last: bool) -> None:
        attributes = json.dumps(
            span.get("attributes", {}), ensure_ascii=True, separators=(",", ":")
        )
        lines.append(f"{prefix}{'`--' if is_last else '|--'} {span.get('name')}")
        detail_prefix = prefix + ("    " if is_last else "|   ")
        lines.append(f"{detail_prefix}| status={span.get('status')}")
        lines.append(f"{detail_prefix}| duration_ms={span.get('duration_ms')}")
        lines.append(f"{detail_prefix}| attributes={attributes}")
        if show_ids:
            lines.append(f"{detail_prefix}| span_id={span.get('span_id')}")
            lines.append(
                f"{detail_prefix}| parent_span_id={span.get('parent_span_id')}"
            )
        span_id = span.get("span_id")
        descendants = children.get(span_id, []) if span_id is not None else []
        child_prefix = prefix + ("    " if is_last else "|   ")
        for index, child in enumerate(descendants):
            visit(child, child_prefix, index == len(descendants) - 1)

    roots = children[None]
    for index, root in enumerate(roots):
        visit(root, "", index == len(roots) - 1)
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path",
        nargs="?",
        default="log/agent-framework/spans",
        help="span JSONL file or directory (default: log/agent-framework/spans)",
    )
    parser.add_argument("--trace-id", help="only show one trace")
    parser.add_argument(
        "--json", action="store_true", help="emit grouped machine-readable JSON"
    )
    parser.add_argument(
        "--show-ids", action="store_true", help="show span IDs in tree output"
    )
    parser.add_argument(
        "--latest", type=int, metavar="N", help="only show the latest N traces"
    )
    args = parser.parse_args()
    records = load(Path(args.path))
    grouped: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        if not args.trace_id or record.get("trace_id") == args.trace_id:
            grouped[str(record.get("trace_id"))].append(record)
    if args.json:
        print(json.dumps(grouped, ensure_ascii=False, indent=2))
        return
    traces = sorted(
        grouped.items(),
        key=lambda item: min(span.get("start_time", "") for span in item[1]),
    )
    if args.latest is not None:
        if args.latest < 1:
            raise SystemExit("--latest must be greater than zero")
        traces = traces[-args.latest :]
    for trace_id, spans in traces:
        started = min(span.get("start_time", "") for span in spans)
        ended = max(span.get("end_time", "") for span in spans)
        print(
            f"\nTrace {trace_id} | spans={len(spans)} "
            f"| start={started} | end={ended}"
        )
        for line in render_trace(spans, show_ids=args.show_ids):
            print(line)


if __name__ == "__main__":
    main()
