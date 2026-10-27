import unittest

from src.digest import oldest_first


class OldestFirstTests(unittest.TestCase):
    def test_respects_limit(self):
        cards = [{"id": i, "waiting_since": i} for i in range(5)]
        self.assertEqual(2, len(oldest_first(cards, 2)))


if __name__ == "__main__":
    unittest.main()
