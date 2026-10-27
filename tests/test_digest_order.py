import unittest

from src.digest import oldest_first


class OldestFirstTests(unittest.TestCase):
    def test_respects_limit(self):
        cards = [{"id": i, "waiting_since": i} for i in range(5)]
        self.assertEqual(2, len(oldest_first(cards, 2)))

    def test_orders_oldest_first(self):
        cards = [
            {"id": "new", "waiting_since": 30},
            {"id": "oldest", "waiting_since": 10},
            {"id": "mid", "waiting_since": 20},
        ]
        result = oldest_first(cards, 2)
        self.assertEqual(["oldest", "mid"], [c["id"] for c in result])


if __name__ == "__main__":
    unittest.main()
