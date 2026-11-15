import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.cogs import music_cog
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


class ContinueQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_continuation_skips_a_track_whose_stream_cannot_be_refreshed(self):
        guild = SimpleNamespace(id=1)
        voice = SimpleNamespace(guild=guild, is_connected=lambda: True)
        bot = SimpleNamespace(guild_config_defaults={"lastfm_enabled": False}, voice_clients=[voice])
        cog = Music(bot)
        session = music_cog.Utilities.Session(guild.id, 2)
        for name in ("first", "broken", "third"):
            session.q.enqueue(name, f"stream-{name}", "", f"source-{name}", 60, 1)
        music_cog.sessions.append(session)
        self.addCleanup(music_cog.sessions.remove, session)

        async def resolve(source_url):
            if source_url == "source-broken":
                raise KeyError("url")
            return {"url": f"fresh-{source_url}"}

        cog.resolve_stream_from_source = resolve
        ctx = SimpleNamespace(guild=guild, channel=SimpleNamespace(send=AsyncMock()))
        probe = AsyncMock(side_effect=RuntimeError("probe reached"))
        with patch.object(music_cog.discord.FFmpegOpusAudio, "from_probe", probe):
            with self.assertRaisesRegex(RuntimeError, "probe reached"):
                await cog.continue_queue(ctx, session.q.current_music, None)

        self.assertEqual(probe.await_args.args[0], "fresh-source-third")
        self.assertIs(session.q.current_music, session.q.queue[2])


if __name__ == "__main__":
    unittest.main()