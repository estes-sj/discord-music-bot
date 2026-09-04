import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "bot" / "utils" / "music_utilities.py"
SPEC = importlib.util.spec_from_file_location("music_utilities_for_tests", MODULE_PATH)
MUSIC_UTILITIES = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MUSIC_UTILITIES
SPEC.loader.exec_module(MUSIC_UTILITIES)


class QueueTests(unittest.TestCase):
    def test_current_track_is_the_same_object_as_the_first_queued_track(self):
        queue = MUSIC_UTILITIES.Queue()
        queue.enqueue("Current", "url-1", "thumb-1", "youtube-1", 60, 1)
        queue.enqueue("Upcoming", "url-2", "thumb-2", "youtube-2", 60, 1)

        self.assertEqual(queue.queued_track_index(queue.current_music), 0)
        self.assertTrue(queue.remove_queued_track(queue.queue[1]))

    def test_moves_an_upcoming_track_after_the_current_track(self):
        queue = MUSIC_UTILITIES.Queue()
        queue.enqueue("Current", "url-1", "thumb-1", "youtube-1", 60, 1)
        queue.enqueue("First", "url-2", "thumb-2", "youtube-2", 60, 1)
        queue.enqueue("Second", "url-3", "thumb-3", "youtube-3", 60, 1)

        self.assertTrue(queue.move_queued_track_after(queue.queue[2], queue.current_music))
        self.assertEqual([track.title for track in queue.queue], ["Current", "Second", "First"])

    def test_shuffle_keeps_current_track_in_place(self):
        queue = MUSIC_UTILITIES.Queue()
        for title in ("Current", "First", "Second", "Third"):
            queue.enqueue(title, f"url-{title}", "thumb", "youtube", 60, 1)

        current_track = queue.current_music
        queue.shuffle_upcoming()

        self.assertIs(queue.queue[0], current_track)
        self.assertEqual({track.title for track in queue.queue}, {"Current", "First", "Second", "Third"})

    def test_clearing_queue_disables_looping(self):
        queue = MUSIC_UTILITIES.Queue()
        queue.enqueue("Current", "url", "thumb", "youtube", 60, 1)
        queue.loop_current = True

        queue.clear_queue()

        self.assertFalse(queue.loop_current)

    def test_playback_position_stops_advancing_while_paused(self):
        queue = MUSIC_UTILITIES.Queue()
        original_monotonic = MUSIC_UTILITIES.time.monotonic
        try:
            MUSIC_UTILITIES.time.monotonic = lambda: 100
            queue.start_playback(15)
            MUSIC_UTILITIES.time.monotonic = lambda: 112
            self.assertEqual(queue.current_position(), 27)

            queue.pause_playback()
            MUSIC_UTILITIES.time.monotonic = lambda: 140
            self.assertEqual(queue.current_position(), 27)

            queue.resume_playback()
            MUSIC_UTILITIES.time.monotonic = lambda: 145
            self.assertEqual(queue.current_position(), 32)
        finally:
            MUSIC_UTILITIES.time.monotonic = original_monotonic


if __name__ == "__main__":
    unittest.main()