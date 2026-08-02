#!/usr/bin/env python3
"""Hook latency benchmark for the achievements plugin.

Measures per-hook wall-clock overhead with realistic payloads so we can
catch performance regressions that functional tests can't (a hook that
adds 50ms to every tool call is invisible to coverage).

Usage:
    python3 scripts/bench_hooks.py            # warm run
    python3 scripts/bench_hooks.py --repeat 5 # more samples
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import __init__ as plugin  # noqa: E402  (repo-root package-dir mapping)


def _plugin_version() -> str:
    import re

    yaml_path = REPO / "plugin.yaml"
    m = re.search(r"^version:\s*([\w.]+)", yaml_path.read_text(), re.M)
    return m.group(1) if m else "?"


def _bench(fn, label: str, repeat: int = 3) -> float:
    # warmup
    fn()
    samples = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1e6)
    med = statistics.median(samples)
    print(f"  {label:<46} {med:9.1f} µs")
    return med


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=3)
    args = ap.parse_args()

    import re

    src = (REPO / "__init__.py").read_text()
    hooks = re.findall(r'register_hook\(\s*[\"\']([\w]+)[\"\']', src)
    print(f"plugin v{_plugin_version()}  hooks={len(hooks)}")
    print(f"benchmarking {args.repeat} samples per hook (median shown)\n")

    # --- realistic payloads -------------------------------------------------
    big_output = ("x" * 1024 * 1024) + "\n"          # 1MB terminal output
    huge_result = "y" * (10 * 1024 * 1024)           # 10MB tool result
    normal_output = "ls\nfile1.txt\nfile2.txt\n"     # typical small output
    normal_result = '{"ok": true, "items": [1, 2, 3]}'

    base_kwargs = dict(
        session_id="bench-session",
        tool_name="terminal",
        args={"command": "ls -la"},
        status="ok",
        duration_ms=42.0,
    )

    print("--- high-frequency hooks (every tool call / every turn) ---")
    _bench(lambda: plugin._pre_tool_call(**base_kwargs), "pre_tool_call", args.repeat)
    _bench(lambda: plugin._post_tool_call(**base_kwargs), "post_tool_call", args.repeat)
    _bench(
        lambda: plugin._transform_terminal_output(output=normal_output, returncode=0, env_type="local"),
        "transform_terminal_output (small)",
        args.repeat,
    )
    _bench(
        lambda: plugin._transform_terminal_output(output=big_output, returncode=0, env_type="local"),
        "transform_terminal_output (1MB)",
        args.repeat,
    )
    _bench(
        lambda: plugin._transform_tool_result(result=normal_result, api_request_id="r1", error_message=None),
        "transform_tool_result (small)",
        args.repeat,
    )
    _bench(
        lambda: plugin._transform_tool_result(result=huge_result, api_request_id="r1", error_message=None),
        "transform_tool_result (10MB)",
        args.repeat,
    )
    _bench(
        lambda: plugin._pre_llm_call(is_first_turn=True, messages=[]),
        "pre_llm_call",
        args.repeat,
    )
    _bench(
        lambda: plugin._post_llm_call(assistant_response="short reply"),
        "post_llm_call",
        args.repeat,
    )
    _bench(
        lambda: plugin._post_llm_call(assistant_response="word " * 1500),
        "post_llm_call (1500 words)",
        args.repeat,
    )
    _bench(
        lambda: plugin._transform_llm_output(response_text="a normal reply", session_id="s", model="m", platform="cli"),
        "transform_llm_output",
        args.repeat,
    )
    _bench(
        lambda: plugin._pre_api_request(base_url="https://api.openai.com", approx_input_tokens=1234, api_mode="chat", max_tokens=2048),
        "pre_api_request",
        args.repeat,
    )
    _bench(
        lambda: plugin._post_api_request(usage={"input_tokens": 100, "output_tokens": 200}, api_duration=1.5, finish_reason="stop", message_count=1),
        "post_api_request",
        args.repeat,
    )

    print("--- lower-frequency hooks (session / subagent / approval) ---")
    _bench(lambda: plugin._on_session_start(session_id="s1"), "on_session_start", args.repeat)
    _bench(lambda: plugin._on_session_end(session_id="s1", model="gpt-4o", platform="cli", message_count=5), "on_session_end", args.repeat)
    _bench(lambda: plugin._on_session_reset(session_id="s1", reason="context_overflow"), "on_session_reset", args.repeat)
    _bench(lambda: plugin._on_session_finalize(session_id="s1"), "on_session_finalize", args.repeat)
    _bench(lambda: plugin._on_subagent_start(child_role="leaf", child_goal="do thing"), "subagent_start", args.repeat)
    _bench(lambda: plugin._on_subagent_stop(child_role="leaf", child_status="completed", duration_ms=5000), "subagent_stop", args.repeat)
    _bench(
        lambda: plugin._on_approval_request(command="rm -rf /", surface="cli", pattern_key="danger", pattern_keys=["danger"], session_key="k"),
        "pre_approval_request",
        args.repeat,
    )
    _bench(
        lambda: plugin._on_approval_response(choice="always", surface="gateway", pattern_key="danger", pattern_keys=["danger"], session_key="k"),
        "post_approval_response",
        args.repeat,
    )
    _bench(lambda: plugin._on_api_request_error(error="timeout", api_duration=30.0), "api_request_error", args.repeat)
    _bench(
        lambda: plugin._on_pre_gateway_dispatch(chat_id="123", thread_id="456", platform="discord", message="hello"),
        "pre_gateway_dispatch",
        args.repeat,
    )

    print("\nThreshold: >2ms on any high-frequency hook is a regression candidate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
