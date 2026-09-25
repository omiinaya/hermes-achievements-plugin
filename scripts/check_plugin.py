#!/usr/bin/env python3
"""Hermes Achievements Plugin — health check.

Verifies the full integrity chain of the plugin:
  1. The plugin loads cleanly (plugin.yaml manifest + __init__.py)
  2. Manifest hooks ↔ register() hooks agree (no drift)
  3. Exactly 154 achievement defs, all with name/description/rarity/group
  4. Locale parity: every def key exists in all 4 locale files
  5. Detection maps contain no dead references (IDs not in defs)
  6. Live state.json (if --live) reconciles: no stale entries, real
     unlocks preserved (the old exactly-105 invariant became dynamic as
     the def set grew)
  7. Real PluginManager load (if --manifest)
  8. Hook kwarg contract vs installed Hermes source (if --gateway):
     every kwargs.get("...") key the plugin reads must be passed by the
     gateway's actual dispatch calls — catches silent no-op drift when
     Hermes renames a hook kwarg.

Usage:
    python3 scripts/check_plugin.py            # check the repo checkout
    python3 scripts/check_plugin.py --live     # also check live Hermes state.json
    python3 scripts/check_plugin.py --manifest # verify via real PluginManager
    python3 scripts/check_plugin.py --gateway  # verify kwarg contract vs Hermes source

Exit code 0 = healthy, 1 = problems found.
"""
import argparse
import ast
import importlib.util
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_FILE = os.path.join(ROOT, "__init__.py")
PLUGIN_YAML = os.path.join(ROOT, "plugin.yaml")
LOCALES_DIR = os.path.join(ROOT, "locales")

# Files in the Hermes installation that dispatch plugin hooks. The AST scan
# below also falls back to a full-tree walk when a hook isn't found in this
# hot list, so a dispatch site that moves to a new file is never silently
# dropped (dispatch sites have migrated several times: post_llm_call now
# fires from turn_finalizer.py, api hooks from turn_api_request.py /
# turn_response_intake.py, session hooks from cli_session_mixin.py).
GATEWAY_SOURCE_CANDIDATES = [
    "agent/conversation_loop.py",
    "agent/turn_finalizer.py",
    "agent/turn_api_request.py",
    "agent/turn_response_intake.py",
    "agent/turn_context.py",
    "agent/inline_tool_executors.py",
    "agent/agent_runtime_helpers.py",
    "agent/api_request_hooks.py",
    "agent/shell_hooks.py",
    "agent/tool_executor.py",
    "gateway/run.py",
    "gateway/slash_commands_session.py",
    "tools/approval.py",
    "tools/delegate_tool.py",
    "tools/terminal_tool.py",
    "model_tools.py",
    "hermes_cli/plugins.py",
    "hermes_cli/plugins_dispatch.py",
    "hermes_cli/lifecycle.py",
    "hermes_cli/cli_session_mixin.py",
    "tui_gateway/session_lifecycle.py",
    "run_agent.py",  # api_request_error dispatches here (invoke_hook literal)
]
HERMES_SOURCE_CANDIDATES = [
    "/usr/local/lib/hermes-agent",
    "/opt/hermes-agent",
    os.path.expanduser("~/hermes-agent"),
]

FAILURES = []


def check(name, ok, detail=""):
    status = "OK " if ok else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(f"{name}: {detail}")


def load_module():
    """Import the plugin without touching the real Hermes state."""
    os.environ.setdefault("HERMES_HOME", os.path.join(ROOT, ".check_home"))
    os.makedirs(os.environ["HERMES_HOME"], exist_ok=True)
    spec = importlib.util.spec_from_file_location("achievements_health_check", PLUGIN_FILE)
    if spec is None or spec.loader is None:
        raise SystemExit("ERROR: could not create module spec for __init__.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["achievements_health_check"] = mod
    spec.loader.exec_module(mod)
    return mod


def manifest_hooks(text):
    """Extract the hook name list from plugin.yaml's block-style hooks list."""
    m = re.search(r"^hooks:\s*\[([^\]]*)\]", text, re.MULTILINE)
    if m:
        return [h.strip().strip("'\"") for h in m.group(1).split(",") if h.strip()]
    # Block style: "hooks:\n  - name\n  - name"
    m = re.search(r"^hooks:\s*\n((?:\s+- .*\n?)+)", text, re.MULTILINE)
    if m:
        return [line.strip().lstrip("- ").strip()
                for line in m.group(1).splitlines()
                if line.strip().startswith("-")]
    return []


def registered_hooks(source):
    """Extract hook names from ctx.register_hook(...) calls in register()."""
    return sorted(set(re.findall(r"register_hook\(\s*[\"']([\w]+)[\"']", source)))


def _read(path):
    """Read a file with a context manager (ruff SIM115)."""
    with open(path, encoding="utf-8") as f:
        return f.read()


def pyproject_version():
    """Read version from pyproject.toml."""
    path = os.path.join(ROOT, "pyproject.toml")
    if not os.path.exists(path):
        return None
    m = re.search(r'^version\s*=\s*"([\d.]+)"', _read(path), re.MULTILINE)
    return m.group(1) if m else None


def _function_body(source, fn_name):
    """Return the module-level def block for fn_name (next col-0 def boundary)."""
    m = re.search(rf"^def {re.escape(fn_name)}\(", source, re.MULTILINE)
    if not m:
        return ""
    nxt = re.search(r"\ndef [a-z_][a-z0-9_]*\(", source[m.end():])
    end = m.end() + nxt.start() if nxt else len(source)
    return source[m.start():end]


def plugin_kwargs_per_hook(source):
    """Map hook name → sorted kwargs.get('...') keys read by its handler."""
    out = {}
    for m in re.finditer(
        # Handlers may be wrapped: register_hook("x", _synchronized(_fn)) —
        # skip the wrapper and capture the real handler name.
        r'register_hook\(\s*["\']([\w]+)["\']\s*,\s*(?:_synchronized\()?([\w_]+)', source
    ):
        hook, fn = m.group(1), m.group(2)
        body = _function_body(source, fn)
        out[hook] = sorted(set(re.findall(r'kwargs\.get\("([a-z_]+)"', body)))
    return out


def _find_hermes_source():
    for cand in HERMES_SOURCE_CANDIDATES:
        if os.path.isdir(cand):
            return cand
    return None


def _call_is_for_hook(call: ast.Call, hook: str) -> bool:
    """True if *call* is a hook dispatch for *hook*.

    The hook name travels as a string constant in the call's args. It is
    usually the FIRST positional arg (``invoke_hook("post_tool_call", ...)``),
    but some sites pass a logger first (``invoke_hook_safely(logger,
    "hook", ...)``) or forward the literal through a helper. To be robust we
    check every POSITIONAL arg (never keywords): a keyword VALUE equal to the
    hook name would be over-broad and could snag an unrelated call.
    """
    for arg in call.args:
        if isinstance(arg, ast.Constant) and arg.value == hook:
            return True
    return False


def _collect_spread_dict_keys(tree: ast.Module, call: ast.Call, found: set) -> None:
    """Collect keys from a dict literal spread via ``**var`` in a dispatch.

    Dispatch sites sometimes build ``hook_kwargs = dict(command=...,
    pattern_keys=..., surface=...)`` in the same function and then call
    ``invoke_hook("pre_approval_request", **hook_kwargs, ...)``. Those keys
    ride inside a variable, invisible to a plain kwarg scan; resolve the
    assignment within the enclosing function scope and add its literal dict
    keys to *found*.
    """
    for kw in call.keywords:
        if kw.arg is not None:
            continue  # plainly-named kwarg already collected by the caller
        # **<Name> — a variable holding a dict literal / dict(...) call
        # **<expr>.method() — a dataclass-derived bag (e.g. _CallIds(...)
        #   .hook_kwargs() returns one key per dataclass field)
        value = kw.value
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute):
            cls = None
            recv = value.func.value
            if isinstance(recv, ast.Call) and isinstance(recv.func, ast.Name):
                cls = recv.func.id
            if cls:
                # Collect the dataclass field names by finding `class <cls>`
                # in this file and reading annotated assignments.
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef) and node.name == cls:
                        for stmt in node.body:
                            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                                found.add(stmt.target.id)
            continue
        target = getattr(value, "id", None)
        if not target:
            continue
        func = None
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            # Does this function contain our dispatch call?
            for inner in ast.walk(node):
                if inner is call:
                    func = node
                    break
            if func is not None:
                break
        if func is None:
            continue
        for stmt in ast.walk(func):
            if (isinstance(stmt, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == target for t in stmt.targets)
                    and isinstance(stmt.value, ast.Dict)):
                for key in stmt.value.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        found.add(key.value)
            # Same for `hook_kwargs = dict(key=..., ...)` constructors.
            if (isinstance(stmt, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == target for t in stmt.targets)
                    and isinstance(stmt.value, ast.Call)
                    and isinstance(stmt.value.func, ast.Name)
                    and stmt.value.func.id == "dict"):
                for kw in stmt.value.keywords:
                    if kw.arg:
                        found.add(kw.arg)


def gateway_kwargs_per_hook(hook, source_root):
    """Collect the kwarg names passed to every dispatch of *hook*.

    AST-based and robust against the two ways the old (regex) scanner
    failed:
      * inline kwargs — ``invoke_hook("post_tool_call", tool_name=...,
        args=...)`` puts kwargs on the SAME line as the hook name; a
        line-anchored regex missed them. We read the whole Call node, so
        inline is fine.
      * stale file list — dispatch sites keep migrating across files; if
        the hot list finds nothing, a full-tree walk catches the moved site.
    """
    found = set()
    seen = set()

    def scan(path):
        if path in seen:
            return
        seen.add(path)
        try:
            source_text = _read(path)
        except OSError:
            return
        try:
            tree = ast.parse(source_text, filename=path)
        except SyntaxError:
            return
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _call_is_for_hook(node, hook):
                for kw in node.keywords:
                    if kw.arg:
                        found.add(kw.arg)
                # Dispatch may spread a pre-built dict (**hook_kwargs /
                # **payload). Those keys live in a dict literal assigned
                # earlier in the same function — collect them so agr
                # approval hooks (pattern_keys, surface, session_id) aren't
                # reported missing just because they ride inside a variable.
                _collect_spread_dict_keys(tree, node, found)

    for rel in GATEWAY_SOURCE_CANDIDATES:
        scan(os.path.join(source_root, rel))

    # Dispatch site may have moved to a file not in the hot list. Walk the
    # app source tree (NOT venv/tests/site-packages — those are huge and
    # never dispatch plugin hooks) to be safe. Only run when the hot list
    # found nothing, keeping the common path fast.
    if not found:
        for root, dirs, files in os.walk(source_root):
            # Prune vendored/irrelevant subtrees so the fallback stays fast.
            dirs[:] = [d for d in dirs if d not in (
                "venv", ".venv", ".git", "node_modules", "tests", "site-packages",
            )]
            for fn in files:
                if fn.endswith(".py"):
                    scan(os.path.join(root, fn))
    return found


def main():
    parser = argparse.ArgumentParser(description="Achievements plugin health check")
    parser.add_argument("--live", action="store_true",
                        help="also check the live Hermes state.json")
    parser.add_argument("--manifest", action="store_true",
                        help="verify the manifest via the real PluginManager")
    parser.add_argument("--gateway", action="store_true",
                        help="verify hook kwarg contract vs installed Hermes source")
    args = parser.parse_args()

    print("── 1. Module loads ──")
    try:
        mod = load_module()
        check("import __init__.py", True,
              f"defs={len(mod.ACHIEVEMENT_DEFS)} hooks={len(registered_hooks(_read(PLUGIN_FILE)))}")
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — report any load failure
        check("import __init__.py", False, repr(exc))
        sys.exit(1)

    print("── 2. Manifest ↔ register() agreement ──")
    if not os.path.exists(PLUGIN_YAML):
        check("plugin.yaml present", False)
        sys.exit(1)
    yaml_text = _read(PLUGIN_YAML)
    # Under mutation testing (mutmut) the repo is copied to mutants/ and the
    # copy's __init__.py is trampoline-instrumented: function names mangled
    # to x__name__mutmut_N, string literals rewritten ("pre_llm_call" →
    # "XXpre_llm_callXX"), MutantDict injected. EVERY source-text check
    # below (manifest↔register, wrapper regex, session-env regex, kwarg
    # contract) would false-fail on that mangled source — it's the harness,
    # not real drift. Detect it once and skip all source-text checks;
    # runtime checks (module load, defs, locales, live state) still run.
    source = _read(PLUGIN_FILE)
    _mutmut_instrumented = "_mutmut_mutated" in source or "MutantDict" in source
    # `declared` comes from plugin.yaml (never instrumented) — needed by the
    # --manifest PluginManager section regardless of mutation state.
    declared = manifest_hooks(yaml_text)
    if _mutmut_instrumented:
        print("  [note] mutmut-instrumented source — skipping source-text "
              "checks (manifest↔register/wrapper/session/kwarg-contract); "
              "runtime checks still run")
    else:
        registered = registered_hooks(source)
        check("manifest declares hooks", len(declared) > 0, f"{len(declared)} hooks")
        check("register() registers hooks", len(registered) > 0, f"{len(registered)} hooks")
        check("no hooks in manifest missing from register()",
              set(declared) <= set(registered),
              f"{sorted(set(declared) - set(registered))}" if set(declared) - set(registered) else "")
        check("no hooks registered but undeclared in manifest",
              set(registered) <= set(declared),
              f"{sorted(set(registered) - set(declared))}" if set(registered) - set(declared) else "")
        # Thread safety: the gateway runs parallel tool calls on worker threads,
        # so every hook/command handler MUST be wrapped in _synchronized (the
        # state lock) — an unwrapped handler risks lost updates on counters.
        unwrapped = sorted(
            set(re.findall(r'register_hook\(\s*["\']([\w]+)["\']\s*,\s*(?!_synchronized\()(\w+)', source))
            | set(re.findall(r'register_command\(\s*["\']([\w-]+)["\']\s*,\s*handler=\s*(?!_synchronized\()(\w+)', source))
        )
        check("all handlers wrapped in _synchronized (thread safety)",
              not unwrapped,
              f"unwrapped: {unwrapped}" if unwrapped else "")
        # Session context: HERMES_SESSION_* live in ContextVars, not os.environ.
        # A raw os.environ read of a session var would silently return "" in
        # gateway contexts (v2.18.9 bug) — require the ContextVar-aware accessor.
        session_env_reads = re.findall(r'os\.environ\.get\("HERMES_SESSION_[\w]+"', source)
        check("session vars read via ContextVar accessor (not raw os.environ)",
              "from gateway.session_context import get_session_env" in source,
              f"raw os.environ session reads: {session_env_reads}" if session_env_reads else
              "missing get_session_env import")
    m = re.search(r"^version:\s*([\d.]+)", yaml_text, re.MULTILINE)
    manifest_version = m.group(1) if m else None
    check("manifest version matches pyproject",
          manifest_version is not None and manifest_version == pyproject_version(),
          f"manifest={manifest_version} pyproject={pyproject_version()}")

    print("── 3. Achievement defs ──")
    defs = mod.ACHIEVEMENT_DEFS
    check("exactly 166 defs", len(defs) == 166, f"{len(defs)} found")
    # pyproject + plugin.yaml descriptions carry the badge count too — it
    # rotted once (146 when the defs reached 154) because nothing guarded it.
    count_ok = True
    for path, what in ((os.path.join(ROOT, "pyproject.toml"), "pyproject"),
                       (os.path.join(ROOT, "plugin.yaml"), "plugin.yaml")):
        m = re.search(r'description\s*[:=]\s*"(\d+) Steam-style', _read(path), re.MULTILINE)
        if m is None or int(m.group(1)) != len(defs):
            count_ok = False
            check(f"{what} description count matches defs", False,
                  f"{what} says {m.group(1) if m else '?'} defs, actual {len(defs)}")
    check("pyproject/plugin.yaml description counts match defs", count_ok, "")
    bad_defs = [aid for aid, d in defs.items()
                if not d.get("name") or not d.get("description")
                or not d.get("rarity") or not d.get("group")]
    check("all defs have name/description/rarity/group", not bad_defs,
          f"{bad_defs}" if bad_defs else "")
    known_rarities = {"common", "uncommon", "rare", "epic", "legendary"}
    bad_rarity = [aid for aid, d in defs.items()
                  if d.get("rarity") not in known_rarities]
    check("all rarities known", not bad_rarity, f"{bad_rarity}" if bad_rarity else "")

    print("── 4. Locale parity ──")
    locale_files = sorted(f for f in os.listdir(LOCALES_DIR) if f.endswith(".json"))
    check("4 locale files", len(locale_files) == 4, ", ".join(locale_files))
    for lf in locale_files:
        try:
            data = json.loads(_read(os.path.join(LOCALES_DIR, lf)))
            ach = data.get("achievement", {})
            missing = [aid for aid in defs if aid not in ach]
            check(f"{lf}: all def keys present", not missing,
                  f"{len(ach)} keys" if not missing else f"missing {missing}")
        except Exception as exc:  # noqa: BLE001
            check(f"{lf}: parses", False, repr(exc))

    print("── 5. Detection maps have no dead references ──")
    dead = set()
    for name in ("_TOOL_ACHIEVEMENTS", "_TOOL_THRESHOLDS", "TERMINAL_PATTERNS"):
        table = getattr(mod, name, {})
        ids = set()
        for v in table.values():
            if isinstance(v, dict):
                for vv in v.values():
                    if isinstance(vv, list):
                        ids.update(i for _, i in vv)
                    elif isinstance(vv, str):
                        ids.add(vv)
            elif isinstance(v, str):
                ids.add(v)
        dead |= {i for i in ids if i not in defs}
    check("no dead achievement IDs in detection maps", not dead,
          f"{sorted(dead)}" if dead else "")

    if args.live:
        print("── 6. Live state.json ──")
        hermes_home = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
        # The plugin persists state at $HERMES_HOME/achievements/state.json
        # (NOT under plugins/) — see _STATE_PATH in __init__.py.
        state_path = os.path.join(hermes_home, "achievements", "state.json")
        if not os.path.exists(state_path):
            check("live state.json present", False, f"not found at {state_path}")
        else:
            try:
                state = json.loads(_read(state_path))
                check("state.json parses", True)
                unlocked = {aid for aid, a in state.get("achievements", {}).items()
                            if isinstance(a, dict) and a.get("unlocked")}
                stale = unlocked - set(defs)
                check("no stale unlocked entries", not stale,
                      f"{sorted(stale)}" if stale else "")
                check("state preserved", len(unlocked) > 0,
                      f"{len(unlocked)} achievements unlocked")
                bak_path = state_path + ".bak"
                # Informational, not a failure: a frozen install written
                # before v2.4.0 (which added the rolling backup) legitimately
                # has no .bak yet — it appears on the first save after load.
                if os.path.exists(bak_path):
                    check("rolling backup present", True)
                else:
                    print("  [note] rolling backup absent — will be created on "
                          "the first save (expected for pre-v2.4.0 frozen state)")
            except Exception as exc:  # noqa: BLE001
                check("state.json parses", False, repr(exc))

    if args.manifest:
        print("── 7. Real PluginManager load ──")
        try:
            # Hermes isn't on the default sys.path when running from a
            # checkout — add the installed source if present.
            for candidate in ("/usr/local/lib/hermes-agent",
                              "/opt/hermes-agent",
                              os.path.expanduser("~/hermes-agent")):
                if os.path.isdir(candidate):
                    sys.path.insert(0, candidate)
                    break
            from hermes_cli.plugins import PluginManager  # type: ignore
            mgr = PluginManager()
            mgr.discover_and_load()
            lp = mgr._plugins.get("achievements")  # SLF001 — inspection tool
            check("PluginManager loads plugin",
                  lp is not None and lp.error is None,
                  f"error={lp.error}" if lp is not None and lp.error else "")
            if lp is not None:
                check("plugin enabled", bool(lp.enabled))
                check("manifest version registered",
                      lp.manifest.version == manifest_version,
                      f"loaded={lp.manifest.version}")
                check("hooks registered",
                      len(lp.hooks_registered) == len(declared),
                      f"{len(lp.hooks_registered)} hooks")
                check("commands registered",
                      len(lp.commands_registered) == 2,
                      f"{lp.commands_registered}")
        except Exception as exc:  # noqa: BLE001
            check("PluginManager load", False,
                  f"{exc} — run from an environment with Hermes installed")

    if args.gateway:
        print("── 8. Hook kwarg contract vs installed Hermes ──")
        source_root = _find_hermes_source()
        if source_root is None:
            print("  [note] Hermes source not found — skipping (run on the "
                  "deployment host: /usr/local/lib/hermes-agent)")
        elif _mutmut_instrumented:
            print("  [note] mutmut-instrumented source — kwarg-contract "
                  "parser cannot read mangled handler names; skipping")
        else:
            reads = plugin_kwargs_per_hook(_read(PLUGIN_FILE))
            total_read = 0
            total_missing = 0
            zero_read = []
            for hook in sorted(reads):
                keys = reads[hook]
                if not keys:
                    # A registered hook whose handler reads NO kwargs may be
                    # intentional (live counters like subagent_start) or a
                    # blind spot (delivered kwargs ignored). Report it so the
                    # choice is visible instead of silently skipping.
                    zero_read.append(hook)
                    continue
                total_read += len(keys)
                passed = gateway_kwargs_per_hook(hook, source_root)
                missing = [k for k in keys if k not in passed]
                total_missing += len(missing)
                check(
                    f"{hook} kwargs passed ({len(keys)} keys)",
                    not missing,
                    "missing: " + ", ".join(missing) if missing else ", ".join(keys),
                )
            if zero_read:
                for hook in zero_read:
                    delivered = gateway_kwargs_per_hook(hook, source_root)
                    note = "reads no kwargs"
                    if delivered:
                        note += f" (gateway delivers: {', '.join(sorted(delivered))})"
                    print(f"  [note] {hook} {note}")
            check(
                "all read kwargs delivered by gateway",
                total_missing == 0,
                f"{total_read} keys checked across {len([h for h in reads if reads[h]])} hooks",
            )

    print()
    if FAILURES:
        print(f"❌ {len(FAILURES)} problem(s) found:")
        for f in FAILURES:
            print(f"   - {f}")
        sys.exit(1)
    print("✅ Plugin healthy.")


if __name__ == "__main__":
    main()
