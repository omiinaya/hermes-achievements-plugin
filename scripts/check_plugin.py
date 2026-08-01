#!/usr/bin/env python3
"""Hermes Achievements Plugin — health check.

Verifies the full integrity chain of the plugin:
  1. The plugin loads cleanly (plugin.yaml manifest + __init__.py)
  2. Manifest hooks ↔ register() hooks agree (no drift)
  3. Exactly 133 achievement defs, all with name/description/rarity/group
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
import importlib.util
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_FILE = os.path.join(ROOT, "__init__.py")
PLUGIN_YAML = os.path.join(ROOT, "plugin.yaml")
LOCALES_DIR = os.path.join(ROOT, "locales")

# Files in the Hermes installation that dispatch plugin hooks.
GATEWAY_SOURCE_CANDIDATES = [
    "agent/conversation_loop.py",
    "gateway/run.py",
    "tools/approval.py",
    "tools/delegate_tool.py",
    "tools/terminal_tool.py",
    "model_tools.py",
    "agent/tool_executor.py",
    "hermes_cli/plugins.py",
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
        r'register_hook\(\s*["\']([\w]+)["\']\s*,\s*([\w_]+)', source
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


def gateway_kwargs_per_hook(hook, source_root):
    """Scan Hermes source for kwargs passed to the hook's dispatch call.

    For each site that references the hook name, walk back to the enclosing
    invoke/emit call and collect `name=value` keyword assignments. Generous
    on purpose: extra gateway kwargs are harmless; a missed real one would
    only cause a false alarm, so we over-collect.
    """
    found = set()
    for rel in GATEWAY_SOURCE_CANDIDATES:
        path = os.path.join(source_root, rel)
        if not os.path.exists(path):
            continue
        lines = _read(path).splitlines()
        for i, ln in enumerate(lines):
            if f'"{hook}"' not in ln:
                continue
            start = i
            while start > 0 and start > i - 40:
                s = lines[start]
                if ("invoke_hook(" in s or "_fire_approval_hook(" in s
                        or "_emit_post_tool_call_hook(" in s):
                    break
                start -= 1
            j = start
            buf = []
            depth = 0
            while j < len(lines) and j <= i + 30:
                buf.append(lines[j])
                depth += lines[j].count("(") - lines[j].count(")")
                j += 1
                if depth <= 0 and j > start + 1:
                    break
            text = "\n".join(buf)
            found |= set(re.findall(r"^\s*([a-z_][a-z0-9_]*)\s*=", text, re.MULTILINE))
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
    declared = manifest_hooks(yaml_text)
    registered = registered_hooks(_read(PLUGIN_FILE))
    check("manifest declares hooks", len(declared) > 0, f"{len(declared)} hooks")
    check("register() registers hooks", len(registered) > 0, f"{len(registered)} hooks")
    check("no hooks in manifest missing from register()",
          set(declared) <= set(registered),
          f"{sorted(set(declared) - set(registered))}" if set(declared) - set(registered) else "")
    check("no hooks registered but undeclared in manifest",
          set(registered) <= set(declared),
          f"{sorted(set(registered) - set(declared))}" if set(registered) - set(declared) else "")
    m = re.search(r"^version:\s*([\d.]+)", yaml_text, re.MULTILINE)
    manifest_version = m.group(1) if m else None
    check("manifest version matches pyproject",
          manifest_version is not None and manifest_version == pyproject_version(),
          f"manifest={manifest_version} pyproject={pyproject_version()}")

    print("── 3. Achievement defs ──")
    defs = mod.ACHIEVEMENT_DEFS
    check("exactly 133 defs", len(defs) == 133, f"{len(defs)} found")
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
        else:
            reads = plugin_kwargs_per_hook(_read(PLUGIN_FILE))
            total_read = 0
            total_missing = 0
            for hook in sorted(reads):
                keys = reads[hook]
                if not keys:
                    continue  # handler counts without reading kwargs (fine)
                total_read += len(keys)
                passed = gateway_kwargs_per_hook(hook, source_root)
                missing = [k for k in keys if k not in passed]
                total_missing += len(missing)
                check(
                    f"{hook} kwargs passed ({len(keys)} keys)",
                    not missing,
                    "missing: " + ", ".join(missing) if missing else ", ".join(keys),
                )
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
