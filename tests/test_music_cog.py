import unittest
from types import SimpleNamespace

from bot.cogs.music_cog import Music


class MusicCogTests(unittest.TestCase):
    def test_channel_with_only_bots_has_no_human_listeners(self):
        channel = SimpleNamespace(
            members=[
                SimpleNamespace(bot=True),
                SimpleNamespace(bot=True),
            ]
        )

        self.assertFalse(Music.channel_has_human_listeners(channel))

    def test_channel_with_a_human_has_human_listeners(self):
        channel = SimpleNamespace(
            members=[
                SimpleNamespace(bot=True),
                SimpleNamespace(bot=False),
            ]
        )

        self.assertTrue(Music.channel_has_human_listeners(channel))


if __name__ == "__main__":
    unittest.main()