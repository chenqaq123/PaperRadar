from __future__ import annotations

import sqlite3
import unittest

from app.db import init_db
from app.importers import import_conference_csv, import_zotero_bibtex
from app.matcher import run_matching


class ImporterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)

    def test_bibtex_import_builds_profiles(self) -> None:
        bib = """
        @article{safe_t2i,
          title={Safe Text-to-Image Diffusion},
          author={Ada Lovelace},
          year={2025},
          abstract={Safety alignment for text-to-image diffusion models.},
          keywords={text-to-image; safety; diffusion}
        }
        """
        summary = import_zotero_bibtex(self.conn, bib)
        self.assertEqual(summary.imported, 1)
        profiles = self.conn.execute("SELECT name FROM interest_profiles").fetchall()
        self.assertIn("All Zotero", [row["name"] for row in profiles])
        self.assertIn("safety", [row["name"] for row in profiles])

    def test_matching_runs_after_imports(self) -> None:
        import_zotero_bibtex(
            self.conn,
            """
            @article{concept_erasure,
              title={Concept Erasure for Text-to-Image Models},
              abstract={Removing unsafe concepts from text-to-image diffusion.},
              keywords={concept erasure; text-to-image}
            }
            """,
        )
        import_conference_csv(
            self.conn,
            "id,title,abstract,authors\n1,Graph-Guided Concept Erasure for Text-to-Image Diffusion Models,Online concept erasure for T2I models,A. Author\n",
            "cvpr",
            2026,
        )
        result = run_matching(self.conn, "cvpr", 2026, 10)
        self.assertGreaterEqual(result["results"], 1)


if __name__ == "__main__":
    unittest.main()
