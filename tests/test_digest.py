import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from digest import suppress_seen


class SuppressSeenTests(unittest.TestCase):
    def test_drops_cards_whose_id_is_in_seen(self):
        cards = [
            {"id": "1", "title": "a"},
            {"id": "2", "title": "b"},
            {"id": "3", "title": "c"},
        ]
        result = suppress_seen(cards, {"2"})
        self.assertEqual(result, [cards[0], cards[2]])

    def test_preserves_order(self):
        cards = [
            {"id": "3", "title": "c"},
            {"id": "1", "title": "a"},
            {"id": "2", "title": "b"},
        ]
        result = suppress_seen(cards, {"1"})
        self.assertEqual(result, [cards[0], cards[2]])

    def test_card_with_no_id_is_always_kept(self):
        cards = [
            {"id": "1", "title": "seen already"},
            {"title": "no id at all"},
            {"id": "2", "title": "not seen"},
        ]
        result = suppress_seen(cards, {"1", "2"})
        self.assertEqual(result, [cards[1]])

    def test_empty_seen_keeps_all_cards(self):
        cards = [{"id": "1", "title": "a"}, {"id": "2", "title": "b"}]
        result = suppress_seen(cards, set())
        self.assertEqual(result, cards)


if __name__ == "__main__":
    unittest.main()
