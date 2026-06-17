from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from storage.embedded_mongo import EmbeddedMongoStore


class StorageTests(unittest.TestCase):
    def test_insert_find_update(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = EmbeddedMongoStore(Path(temp_dir) / "mongo.json")
            doc = store.insert_one("runs", {"stage": "extract", "status": "success"})
            self.assertIn("_id", doc)
            found = store.find_one("runs", {"stage": "extract"})
            self.assertEqual(found["status"], "success")
            updated = store.update_one("runs", {"stage": "extract"}, {"$set": {"status": "done"}})
            self.assertEqual(updated["status"], "done")


if __name__ == "__main__":
    unittest.main()

