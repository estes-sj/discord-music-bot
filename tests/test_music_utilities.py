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


if __name__ == "__main__":
    unittest.main()