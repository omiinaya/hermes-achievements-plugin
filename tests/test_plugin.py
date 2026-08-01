#!/usr/bin/env python3
"""Test suite for the Hermes Achievements Plugin.

Run with:  python3 -m pytest tests/  -xvs
           python3 tests/test_plugin.py   (direct)
"""
import json
import os
import re
import sys
import unittest

# ── Paths ───────────────────────────────────────────────────────────────────
PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCALES_DIR = os.path.join(PLUGIN_DIR, "locales")
PLUGIN_FILE = os.path.join(PLUGIN_DIR, "__init__.py")

# Load the plugin module in a controlled way (hooks won't fire without context)
sys.path.insert(0, PLUGIN_DIR)
sys.path.insert(0, os.path.join(PLUGIN_DIR, ".."))  # plugins dir


def _load_locales():
    """Load all locale files from disk."""
    locales = {}
    for fname in sorted(os.listdir(LOCALES_DIR)):
        if fname.endswith(".json"):
            lang = fname[:-5]
            with open(os.path.join(LOCALES_DIR, fname), encoding="utf-8") as f:
                locales[lang] = json.load(f)
    return locales


def _parse_achievement_defs():
    """Parse ACHIEVEMENT_DEFS from the plugin source without importing."""
    with open(PLUGIN_FILE, encoding="utf-8") as f:
        content = f.read()

    start = content.find("ACHIEVEMENT_DEFS = {")
    if start < 0:
        return []

    # Find the matching closing brace
    defs_start = content.index("{", start) + 1
    depth = 1
    i = defs_start
    while depth > 0 and i < len(content):
        if content[i] == "{":
            depth += 1
        elif content[i] == "}":
            depth -= 1
        i += 1
    defs_text = content[defs_start : i - 1]

    # Parse individual blocks
    achievements = []
    current = ""
    depth = 0
    for c in defs_text:
        if c == "{":
            depth += 1
            if depth == 1:
                current = c
            else:
                current += c
        elif c == "}":
            depth -= 1
            current += c
            if depth == 0 and current.strip():
                fields = {}
                for key in ("id", "name", "description", "emoji", "rarity", "group"):
                    m = re.search(r'"' + key + r'":\s*"([^"]+)"', current)
                    fields[key] = m.group(1) if m else ""
                if fields.get("id"):
                    achievements.append(fields)
                current = ""
        else:
            if depth >= 1:
                current += c
    return achievements


def _parse_groups_and_rarities():
    """Extract GROUPS list and RARITY_EMOJIS from source."""
    with open(PLUGIN_FILE, encoding="utf-8") as f:
        content = f.read()

    groups = re.search(r'GROUPS\s*=\s*\[(.*?)\]', content, re.DOTALL)
    groups_list = re.findall(r'"([^"]+)"', groups.group(1)) if groups else []

    rarities_m = re.search(r'RARITY_EMOJIS\s*=\s*\{(.*?)\}', content, re.DOTALL)
    # RARITY_EMOJIS keys are the rareness values; filter to get just the keys
    # Keys are before the colon
    rarities = []
    if rarities_m:
        rarities = list(re.findall(r'"(\w+)"\s*:', rarities_m.group(1)))

    return groups_list, rarities


# ═══════════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestAchievementDefinitions(unittest.TestCase):
    """Validate that all 107 achievement definitions are complete and valid."""

    def setUp(self):
        self.achievements = _parse_achievement_defs()
        self.groups, self.rarities = _parse_groups_and_rarities()

    def test_exact_count(self):
        """There should be exactly 107 achievements."""
        self.assertEqual(len(self.achievements), 107,
                         f"Expected 107 achievements, got {len(self.achievements)}")

    def test_required_fields(self):
        """Every achievement must have id, name, description, emoji, rarity, group."""
        required = {"id", "name", "description", "emoji", "rarity", "group"}
        for ach in self.achievements:
            missing = required - set(ach.keys())
            empty = [k for k in required if not ach.get(k)]
            self.assertEqual(len(missing), 0,
                             f"{ach.get('id', '??')} missing keys: {missing}")
            self.assertEqual(len(empty), 0,
                             f"{ach['id']} empty fields: {empty}")

    def test_unique_ids(self):
        """All achievement IDs must be unique."""
        ids = [a["id"] for a in self.achievements]
        dupes = {i for i in ids if ids.count(i) > 1}
        self.assertEqual(len(dupes), 0, f"Duplicate IDs: {dupes}")

    def test_valid_groups(self):
        """Every achievement's group must be one of the defined GROUPS."""
        for ach in self.achievements:
            self.assertIn(ach["group"], self.groups,
                          f"{ach['id']} has unknown group '{ach['group']}'")

    def test_valid_rarities(self):
        """Every achievement's rarity must be one of the defined rarities."""
        for ach in self.achievements:
            self.assertIn(ach["rarity"], self.rarities,
                          f"{ach['id']} has unknown rarity '{ach['rarity']}'")

    def test_group_counts(self):
        """Verify the expected distribution of achievements across groups."""
        from collections import Counter
        counts = Counter(a["group"] for a in self.achievements)
        expected = {
            "Getting Started": 11,
            "Tools & Skills": 28,
            "Power User": 24,
            "Expert": 19,
            "Milestones": 19,
            "Community": 6,
        }
        for group, expected_count in expected.items():
            self.assertEqual(counts.get(group, 0), expected_count,
                             f"Group '{group}': expected {expected_count}, got {counts.get(group, 0)}")
        # Verify no groups outside the expected set
        for group in counts:
            self.assertIn(group, expected, f"Unexpected group '{group}' with {counts[group]} achievements")


class TestLocaleConsistency(unittest.TestCase):
    """Validate that all locale files are complete, consistent, and valid."""

    def setUp(self):
        self.locales = _load_locales()
        self.achievements = _parse_achievement_defs()
        self.groups, self.rarities = _parse_groups_and_rarities()

    def test_all_locales_present(self):
        """Must have locale files for en, es, fr, pt."""
        for lang in ("en", "es", "fr", "pt"):
            self.assertIn(lang, self.locales, f"Missing locale: {lang}")

    def test_locale_structure(self):
        """Every locale must have locale, achievement, group, rarity, ui keys."""
        for lang, data in self.locales.items():
            for key in ("locale", "achievement", "group", "rarity", "ui"):
                self.assertIn(key, data,
                              f"{lang} missing top-level key '{key}'")

    def test_all_achievements_translated(self):
        """Every achievement must have name+description in every locale."""
        for lang, data in self.locales.items():
            ach_data = data.get("achievement", {})
            for ach in self.achievements:
                aid = ach["id"]
                self.assertIn(aid, ach_data,
                              f"{lang} missing achievement '{aid}'")
                self.assertIn("name", ach_data[aid],
                              f"{lang}.{aid} missing 'name'")
                self.assertIn("description", ach_data[aid],
                              f"{lang}.{aid} missing 'description'")
                # Description must not be empty
                self.assertTrue(len(ach_data[aid]["description"]) > 0,
                                f"{lang}.{aid}.description is empty")

    def test_all_groups_translated(self):
        """Every group must have a translation in every locale."""
        for lang, data in self.locales.items():
            group_data = data.get("group", {})
            for g in self.groups:
                key = g.lower().replace(" & ", "_").replace(" ", "_")
                self.assertIn(key, group_data,
                              f"{lang} missing group '{key}' ({g})")
                self.assertTrue(len(group_data[key]) > 0,
                                f"{lang}.group.{key} is empty")

    def test_all_rarities_translated(self):
        """Every rarity must have a translation in every locale."""
        for lang, data in self.locales.items():
            rarity_data = data.get("rarity", {})
            for r in self.rarities:
                self.assertIn(r, rarity_data,
                              f"{lang} missing rarity '{r}'")
                self.assertTrue(len(rarity_data[r]) > 0,
                                f"{lang}.rarity.{r} is empty")

    def test_key_parity_across_locales(self):
        """All locale files must have identical key sets at every level."""
        en_keys = {
            "achievement": set(self.locales["en"]["achievement"].keys()),
            "group": set(self.locales["en"]["group"].keys()),
            "rarity": set(self.locales["en"]["rarity"].keys()),
            "ui": set(self.locales["en"]["ui"].keys()),
        }
        for lang, data in self.locales.items():
            if lang == "en":
                continue
            for section in ("achievement", "group", "rarity", "ui"):
                expected = en_keys[section]
                actual = set(data.get(section, {}).keys())
                missing = expected - actual
                extra = actual - expected
                self.assertEqual(len(missing), 0,
                                 f"{lang}.{section} missing keys: {missing}")
                self.assertEqual(len(extra), 0,
                                 f"{lang}.{section} extra keys: {extra}")

    def test_ui_key_referenced_by_code(self):
        """Every 'ui.' key call in the plugin code has a matching locale entry."""
        with open(PLUGIN_FILE, encoding="utf-8") as f:
            content = f.read()
        # Find all _t("ui.xxx" calls
        ui_refs = set(re.findall(r'_t\("ui\.([^"]+)"', content))
        en_ui_keys = set(self.locales["en"]["ui"].keys())
        missing = ui_refs - en_ui_keys
        self.assertEqual(len(missing), 0,
                         f"UI keys referenced in code but missing from en.json: {missing}")

    def test_no_empty_translations(self):
        """No translation in any locale should be an empty string."""
        for lang, data in self.locales.items():
            for section in ("achievement", "group", "rarity", "ui"):
                for key, val in data.get(section, {}).items():
                    if isinstance(val, str):
                        self.assertTrue(len(val) > 0,
                                        f"{lang}.{section}.{key} is empty")
                    elif isinstance(val, dict):
                        for subkey, subval in val.items():
                            if isinstance(subval, str):
                                self.assertTrue(len(subval) > 0,
                                                f"{lang}.{section}.{key}.{subkey} is empty")


class TestTranslationFunction(unittest.TestCase):
    """Test the _t() translation logic end-to-end."""

    def setUp(self):
        self.locales = _load_locales()

    def _t(self, key, locale="en", **kwargs):
        """Replicates the plugin's _t() function."""
        parts = key.split(".")
        val = None
        for lang in (locale, "en"):
            data = self.locales.get(lang, {})
            v = data
            for part in parts:
                if isinstance(v, dict):
                    v = v.get(part)
                else:
                    v = None
                    break
            if isinstance(v, str):
                val = v
                break
        if val is None:
            return key
        if kwargs:
            try:
                val = val.format(**kwargs)
            except (KeyError, ValueError):
                pass
        return val

    def test_english(self):
        """English locale returns correct values."""
        self.assertEqual(self._t("achievement.first_steps.name", "en"), "First Steps")
        self.assertEqual(self._t("group.getting_started", "en"), "Getting Started")
        self.assertEqual(self._t("rarity.legendary", "en"), "Legendary")

    def test_spanish(self):
        """Spanish locale returns translated values."""
        name = self._t("achievement.first_steps.name", "es")
        self.assertNotEqual(name, "First Steps", "Spanish translation missing")
        self.assertTrue(len(name) > 0)

    def test_french(self):
        """French locale returns translated values."""
        name = self._t("achievement.first_steps.name", "fr")
        self.assertNotEqual(name, "First Steps", "French translation missing")
        self.assertTrue(len(name) > 0)

    def test_portuguese(self):
        """Portuguese locale returns translated values."""
        name = self._t("achievement.first_steps.name", "pt")
        self.assertNotEqual(name, "First Steps", "Portuguese translation missing")
        self.assertTrue(len(name) > 0)

    def test_format_kwargs(self):
        """_t() should substitute {placeholder} values."""
        result = self._t("ui.stats_unlocked", "en",
                         unlocked=5, total=100, percent=5)
        self.assertIn("5", result)
        self.assertIn("100", result)

    def test_fallback_to_english(self):
        """Unknown locale falls back to English."""
        result = self._t("achievement.first_steps.name", "zz")
        self.assertEqual(result, "First Steps")

    def test_unknown_key_returns_key(self):
        """Unknown key should return the key itself."""
        result = self._t("nonexistent.key", "en")
        self.assertEqual(result, "nonexistent.key")

    def test_all_ui_strings_format(self):
        """All UI strings with {placeholders} should format without error."""
        # Keys that need specific format args
        formatters = {
            "stats_unlocked": {"unlocked": 0, "total": 107, "percent": 0},
            "stats_total_turns": {"count": 42},
            "stats_tokens": {"count": 12345},
            "stats_unique_tools": {"count": 10},
            "stats_total_calls": {"count": 500},
            "stats_sessions": {"count": 5},
            "stats_streak": {"count": 7},
            "stats_session_tools": {"calls": 12, "tools": 6},
            "stats_cron": {"count": 3},
            "stats_skills_created": {"count": 2},
            "stats_config": {"count": 9},
            "stats_delegated": {"count": 4},
            "stats_approvals_always": {"count": 2},
            "stats_approvals_denied": {"count": 1},
            "stats_approval_requests": {"count": 4},
            "stats_max_concurrent": {"count": 2},
            "stats_api_errors": {"count": 3},
            "stats_users_seen": {"count": 4},
            "stats_session_resets": {"count": 3},
            "stats_hooks_used": {"count": 4},
            "stats_platforms": {"platforms": "discord, telegram"},
            "stats_models": {"models": "gpt4", "more": ""},
            "stats_providers": {"providers": "openrouter, openai", "more": ""},
            "model_more": {"count": 3},
            "group_header": {"emoji": "🚀", "group": "Test", "unlocked": 5, "total": 10},
            "group_filter_header": {"emoji": "🚀", "group": "Test"},
            "detail_progress": {"bar": "█████░░░░░", "current": 5, "target": 10, "percent": 50},
            "detail_unlocked_at": {"time": "2026-01-01T00:00:00"},
            "detail_rarity": {"emoji": "⬜", "rarity": "Common"},
            "detail_group": {"group": "Test"},
            "detail_status_locked": {},
            "detail_status_format": {"status_icon": "⬜", "emoji": "👣", "name": "Test",
                                      "description": "Test desc", "rarity_line": "",
                                      "group_line": "", "status_line": ""},
            "help_footer": {"all": "a", "filter": "b", "overview": "c", "latest": "d", "detail": "e", "next_up": "f"},
            "lang_set": {"lang": "es", "native": "Español"},
            "lang_invalid": {"code": "xx"},
            "notification_format": {"emoji": "⬜", "icon": "👣", "name": "Test",
                                     "description": "Test", "rarity": "Common", "group": "Test"},
            "batch_unlocked": {"count": 3},
            "next_hint": {"name": "Test", "bar": "█████░░░░░", "current": 5, "target": 10, "percent": 50},
            "badge_format": {"icon": "⬜", "name": "Test", "description": "Test", "progress": ""},
            "stats_header": {},
            "stats_footer": {},
            "newly_prefix": {},
        }

        en_ui = self.locales["en"]["ui"]
        for key, tmpl in en_ui.items():
            fmt_args = formatters.get(key, {})
            try:
                tmpl.format(**fmt_args)
            except KeyError as e:
                # If we missed a formatter, flag it
                self.fail(f"en.ui.{key} has unmet format key {e}")


class TestInstallScript(unittest.TestCase):
    """Validate setup.sh is present and well-formed."""

    def test_setup_script_exists(self):
        """setup.sh must be present."""
        path = os.path.join(PLUGIN_DIR, "setup.sh")
        self.assertTrue(os.path.exists(path), "setup.sh not found")

    def test_setup_script_shebang(self):
        """setup.sh must have a bash shebang."""
        path = os.path.join(PLUGIN_DIR, "setup.sh")
        with open(path) as f:
            first_line = f.readline().strip()
        self.assertTrue(first_line.startswith("#!/"), f"Bad shebang: {first_line}")

    def test_setup_script_executable(self):
        """setup.sh should be executable."""
        path = os.path.join(PLUGIN_DIR, "setup.sh")
        self.assertTrue(os.access(path, os.X_OK), "setup.sh is not executable")


class TestFileIntegrity(unittest.TestCase):
    """Validate all expected repo files exist and are non-empty."""

    REQUIRED_FILES: tuple[str, ...] = (
        "__init__.py",
        "plugin.yaml",
        "pyproject.toml",
        "README.md",
        "CHANGELOG.md",
        "LICENSE",
        ".gitignore",
        "setup.sh",
        "locales/en.json",
        "locales/es.json",
        "locales/fr.json",
        "locales/pt.json",
    )

    def test_all_files_exist(self):
        """All required files must exist and be non-empty."""
        for fname in self.REQUIRED_FILES:
            path = os.path.join(PLUGIN_DIR, fname)
            self.assertTrue(os.path.exists(path), f"Missing file: {fname}")
            self.assertGreater(os.path.getsize(path), 0,
                               f"Empty file: {fname}")

    def test_plugin_yaml_version(self):
        """plugin.yaml version must match pyproject.toml."""
        with open(os.path.join(PLUGIN_DIR, "plugin.yaml")) as f:
            yaml_content = f.read()
        with open(os.path.join(PLUGIN_DIR, "pyproject.toml")) as f:
            toml_content = f.read()

        yaml_ver = re.search(r'version:\s*([\d.]+)', yaml_content)
        toml_ver = re.search(r'^\s*version\s*=\s*"([\d.]+)"', toml_content, re.MULTILINE)

        self.assertIsNotNone(yaml_ver, "No version in plugin.yaml")
        self.assertIsNotNone(toml_ver, "No version in pyproject.toml")
        self.assertEqual(yaml_ver.group(1), toml_ver.group(1),
                         f"Version mismatch: plugin.yaml={yaml_ver.group(1)} vs pyproject.toml={toml_ver.group(1)}")


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main(verbosity=2)
