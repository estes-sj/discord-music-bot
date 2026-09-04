import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock


MODULE_PATH = Path(__file__).parents[1] / "bot" / "utils" / "command_references.py"
SPEC = importlib.util.spec_from_file_location("command_references_for_tests", MODULE_PATH)
COMMAND_REFERENCES = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = COMMAND_REFERENCES
SPEC.loader.exec_module(COMMAND_REFERENCES)


class CommandReferenceTests(unittest.TestCase):
    def configure(self, slash_enabled, prefix_enabled, prefix="!"):
        return {
            "slash_commands_enabled": slash_enabled,
            "prefix_commands_enabled": prefix_enabled,
            "command_prefix": prefix,
        }

    def test_combines_slash_command_and_prefix_alias(self):
        config = self.configure(slash_enabled=True, prefix_enabled=True)

        reference = COMMAND_REFERENCES.format_command_reference(config, "queue", "q")

        self.assertEqual(reference, "`/queue` or `!q`")

    def test_returns_only_slash_command_when_prefix_commands_are_disabled(self):
        config = self.configure(slash_enabled=True, prefix_enabled=False)

        reference = COMMAND_REFERENCES.format_command_reference(config, "queue", "q")

        self.assertEqual(reference, "`/queue`")

    def test_returns_only_configured_prefix_when_slash_commands_are_disabled(self):
        config = self.configure(slash_enabled=False, prefix_enabled=True, prefix="?")

        reference = COMMAND_REFERENCES.format_command_reference(config, "queue", "q")

        self.assertEqual(reference, "`?q`")

    def test_guildless_config_uses_defaults_without_querying_store(self):
        store = Mock()
        defaults = self.configure(slash_enabled=True, prefix_enabled=True)

        config = COMMAND_REFERENCES.load_guild_config(store, None, defaults)

        self.assertEqual(config, defaults)
        self.assertIsNot(config, defaults)
        store.get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
