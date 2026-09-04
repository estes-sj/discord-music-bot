import importlib.util
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "bot" / "views" / "config_views.py"
SPEC = importlib.util.spec_from_file_location("config_views_for_tests", MODULE_PATH)
CONFIG_VIEWS = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CONFIG_VIEWS
SPEC.loader.exec_module(CONFIG_VIEWS)


def test_guild_config_modal_has_five_valid_rows():
    modal = CONFIG_VIEWS.GuildConfigModal
    fields = [
        modal.command_prefix,
        modal.command_modes,
        modal.disconnect,
        modal.playlist,
        modal.features,
    ]

    assert len(fields) == 5
    assert all(1 <= len(field.to_component_dict()["label"]) <= 45 for field in fields)