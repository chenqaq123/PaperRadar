from __future__ import annotations

import unittest
from unittest import mock

from app import zotero_api
from app.zotero_api import _clean_creators, _paper_item_for_connector, _parse_creators


class CreatorSanitizationTests(unittest.TestCase):
    """The Zotero Connector returns HTTP 500 on /saveItems when an item carries a
    creator with no usable name. These guards keep such creators out of the payload."""

    def test_clean_creators_drops_nameless_entries(self) -> None:
        creators = [
            {"creatorType": "author", "firstName": "Ada", "lastName": "Lovelace"},
            {"creatorType": "author", "firstName": "", "lastName": ""},
            {"creatorType": "author", "firstName": " ", "lastName": " "},
            {"creatorType": "author", "name": ""},
            {"creatorType": "author"},
        ]
        cleaned = _clean_creators(creators)
        self.assertEqual(cleaned, [{"creatorType": "author", "firstName": "Ada", "lastName": "Lovelace"}])

    def test_parse_creators_never_emits_blank(self) -> None:
        for raw in ("", " , ", ";;", " and ", " | "):
            for creator in _parse_creators(raw):
                self.assertTrue(
                    creator["firstName"].strip() or creator["lastName"].strip(),
                    f"blank creator from {raw!r}",
                )

    def test_connector_item_strips_blank_creators(self) -> None:
        item = {
            "itemType": "conferencePaper",
            "title": "T",
            "creators": [
                {"creatorType": "author", "firstName": "", "lastName": ""},
                {"creatorType": "author", "firstName": "Ada", "lastName": "Lovelace"},
            ],
            "collections": ["C17"],
            "_pdf_url": "",
        }
        result = _paper_item_for_connector(item, 0)
        self.assertEqual(result["creators"], [{"creatorType": "author", "firstName": "Ada", "lastName": "Lovelace"}])


class TransientRetryTests(unittest.TestCase):
    """The Zotero Connector returns intermittent empty-bodied HTTP 500s while busy;
    the same item succeeds on retry. _save_items_with_retry must tolerate that."""

    def setUp(self) -> None:
        sleep = mock.patch.object(zotero_api.time, "sleep", lambda *_: None)
        sleep.start()
        self.addCleanup(sleep.stop)

    def test_retries_transient_500_then_succeeds(self) -> None:
        calls = {"n": 0}

        def flaky(items, target):
            calls["n"] += 1
            if calls["n"] < 3:
                raise ValueError("Zotero Connector error 500: ")
            return "session-ok"

        with mock.patch.object(zotero_api, "_post_save_items", side_effect=flaky):
            session = zotero_api._save_items_with_retry({"creators": [{"lastName": "X"}]}, "C1")
        self.assertEqual(session, "session-ok")
        self.assertEqual(calls["n"], 3)

    def test_persistent_500_falls_back_to_no_creators(self) -> None:
        seen = []

        def always_500_with_creators(items, target):
            seen.append(bool(items[0].get("creators")))
            if items[0].get("creators"):
                raise ValueError("Zotero Connector error 500: ")
            return "session-no-creators"

        with mock.patch.object(zotero_api, "_post_save_items", side_effect=always_500_with_creators):
            session = zotero_api._save_items_with_retry({"creators": [{"lastName": "X"}]}, "C1")
        self.assertEqual(session, "session-no-creators")
        self.assertIn(False, seen)

    def test_non_500_error_is_not_retried(self) -> None:
        def bad_collection(items, target):
            raise ValueError('Zotero Connector error 400: {"error":"COLLECTION_NOT_FOUND"}')

        with mock.patch.object(zotero_api, "_post_save_items", side_effect=bad_collection):
            with self.assertRaises(ValueError):
                zotero_api._save_items_with_retry({"creators": []}, "C1")


if __name__ == "__main__":
    unittest.main()
