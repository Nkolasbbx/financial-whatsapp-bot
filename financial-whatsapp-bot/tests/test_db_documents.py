import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from db.documents import delete_ingested_document, get_stale_ingestion_jobs


def _fake_dependencies_with(conn):
    db_pool = MagicMock()
    db_pool.getconn.return_value = conn
    return db_pool, SimpleNamespace(db_pool=db_pool)


class DeleteIngestedDocumentTests(unittest.TestCase):
    def test_deletes_document_rows_and_marks_job_as_deleted(self):
        conn = MagicMock()
        cur = MagicMock()
        cur.rowcount = 3
        conn.cursor.return_value.__enter__.return_value = cur
        db_pool, fake_dependencies = _fake_dependencies_with(conn)

        with patch.dict(sys.modules, {"dependencies": fake_dependencies}):
            result = delete_ingested_document("tramite.md", "job-1", "InnovaRecoleta")

        self.assertEqual(result, 3)
        conn.commit.assert_called_once()
        db_pool.putconn.assert_called_once_with(conn)

        delete_sql = cur.execute.call_args_list[0].args[0]
        update_sql = cur.execute.call_args_list[1].args[0]
        update_params = cur.execute.call_args_list[1].args[1]
        self.assertIn("DELETE FROM documents", delete_sql)
        self.assertIn("UPDATE ingestion_jobs", update_sql)
        self.assertIn("deleted_at = now()", update_sql)
        self.assertEqual(update_params, ("InnovaRecoleta", "job-1"))

    def test_rolls_back_and_reraises_on_failure(self):
        conn = MagicMock()
        cur = MagicMock()
        cur.execute.side_effect = RuntimeError("db down")
        conn.cursor.return_value.__enter__.return_value = cur
        _db_pool, fake_dependencies = _fake_dependencies_with(conn)

        with patch.dict(sys.modules, {"dependencies": fake_dependencies}):
            with self.assertRaises(RuntimeError):
                delete_ingested_document("tramite.md", "job-1", "InnovaRecoleta")

        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()


class GetStaleIngestionJobsTests(unittest.TestCase):
    def test_returns_stale_jobs_as_plain_dicts(self):
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchall.return_value = [{"id": "job-1", "file_name": "a.md"}]
        conn.cursor.return_value.__enter__.return_value = cur
        _db_pool, fake_dependencies = _fake_dependencies_with(conn)

        with patch.dict(sys.modules, {"dependencies": fake_dependencies}):
            result = get_stale_ingestion_jobs(45)

        self.assertEqual(result, [{"id": "job-1", "file_name": "a.md"}])
        query = cur.execute.call_args.args[0]
        params = cur.execute.call_args.args[1]
        self.assertIn("status IN ('queued', 'processing')", query)
        self.assertEqual(params, (45,))

    def test_defaults_to_sixty_minutes(self):
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchall.return_value = []
        conn.cursor.return_value.__enter__.return_value = cur
        _db_pool, fake_dependencies = _fake_dependencies_with(conn)

        with patch.dict(sys.modules, {"dependencies": fake_dependencies}):
            get_stale_ingestion_jobs()

        self.assertEqual(cur.execute.call_args.args[1], (60,))


if __name__ == "__main__":
    unittest.main()
