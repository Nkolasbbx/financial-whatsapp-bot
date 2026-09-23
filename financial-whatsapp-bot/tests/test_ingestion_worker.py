import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import dependencies
import worker


class ProcessDocumentIngestionTaskTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fake_supabase_admin = MagicMock()
        self.fake_supabase_admin.storage.from_.return_value.download.return_value = b"# Titulo\n\ncontenido"
        patcher = patch.object(dependencies, "supabase_admin", self.fake_supabase_admin)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_markdown_success_path_marks_job_done(self):
        rows = [{"content": "p", "embed_text": "texto a embeber", "meta": {}}]

        with patch.object(worker, "process_document_to_rows", return_value=rows) as rows_mock, \
             patch.object(worker, "embed_batch_remoto", AsyncMock(return_value=[[0.1, 0.2]])) as embed_mock, \
             patch.object(worker, "upsert_document", return_value=1) as upsert_mock, \
             patch.object(worker, "update_ingestion_job") as update_mock, \
             patch.object(worker, "extract_pdf_to_markdown") as extract_mock:
            await worker.process_document_ingestion_task(
                {},
                job_id="job-1",
                storage_path="hash/doc.md",
                file_name="doc.md",
                content_type="text/markdown",
                comuna="Recoleta",
                rubros=["general"],
                vigencia_desde=None,
                vigencia_hasta=None,
                content_hash="hash",
                uploaded_by="InnovaRecoleta",
            )

        extract_mock.assert_not_called()  # no es PDF, no debe intentar extraer
        rows_mock.assert_called_once()
        embed_mock.assert_awaited_once_with(["texto a embeber"], prefix="passage")
        upsert_mock.assert_called_once_with("doc.md", rows)

        status_calls = [c.kwargs for c in update_mock.call_args_list]
        self.assertEqual(status_calls[0], {"status": "processing"})
        self.assertEqual(status_calls[-1]["status"], "done")
        self.assertEqual(status_calls[-1]["chunks_written"], 1)
        self.assertFalse(status_calls[-1]["review_flag"])

        self.fake_supabase_admin.storage.from_.return_value.remove.assert_called_once_with(["hash/doc.md"])

    async def test_pdf_path_extracts_before_chunking_and_flags_review(self):
        self.fake_supabase_admin.storage.from_.return_value.download.return_value = b"%PDF-fake-bytes"
        rows = [{"content": "p", "embed_text": "child", "meta": {}}]

        with patch.object(worker, "extract_pdf_to_markdown", return_value=("markdown", [{"page": 3}])) as extract_mock, \
             patch.object(worker, "process_document_to_rows", return_value=rows) as rows_mock, \
             patch.object(worker, "embed_batch_remoto", AsyncMock(return_value=[[0.1]])), \
             patch.object(worker, "upsert_document", return_value=1), \
             patch.object(worker, "update_ingestion_job") as update_mock, \
             patch("builtins.open", MagicMock()), \
             patch("os.path.exists", return_value=True), \
             patch("os.remove") as remove_mock:
            await worker.process_document_ingestion_task(
                {},
                job_id="job-2",
                storage_path="hash/doc.pdf",
                file_name="doc.pdf",
                content_type="application/pdf",
                comuna="Recoleta",
                rubros=["general"],
                vigencia_desde=None,
                vigencia_hasta=None,
                content_hash="hash",
                uploaded_by="InnovaRecoleta",
            )

        extract_mock.assert_called_once()
        rows_mock.assert_called_once()
        self.assertEqual(rows_mock.call_args.args[2], "pdf")
        remove_mock.assert_called_once()  # limpia el archivo temporal

        final_call = update_mock.call_args_list[-1].kwargs
        self.assertEqual(final_call["status"], "done")
        self.assertTrue(final_call["review_flag"])
        self.assertEqual(final_call["review_details"], {"flagged_pages": [{"page": 3}]})

    async def test_failure_marks_job_failed_reraises_and_still_cleans_storage(self):
        with patch.object(worker, "process_document_to_rows", side_effect=RuntimeError("boom")), \
             patch.object(worker, "update_ingestion_job") as update_mock:
            with self.assertRaises(RuntimeError):
                await worker.process_document_ingestion_task(
                    {},
                    job_id="job-3",
                    storage_path="hash/doc.md",
                    file_name="doc.md",
                    content_type="text/markdown",
                    comuna="Recoleta",
                    rubros=["general"],
                    vigencia_desde=None,
                    vigencia_hasta=None,
                    content_hash="hash",
                    uploaded_by="InnovaRecoleta",
                )

        final_call = update_mock.call_args_list[-1].kwargs
        self.assertEqual(final_call["status"], "failed")
        self.assertIn("boom", final_call["error_message"])
        self.fake_supabase_admin.storage.from_.return_value.remove.assert_called_once_with(["hash/doc.md"])

    async def test_original_error_propagates_even_if_marking_failed_also_fails(self):
        # Caso real: worker sin pool de Postgres, todo update_ingestion_job revienta.
        with patch.object(worker, "update_ingestion_job", side_effect=AttributeError("no pool")):
            with self.assertRaises(AttributeError):
                await worker.process_document_ingestion_task(
                    {},
                    job_id="job-4",
                    storage_path="hash/doc.md",
                    file_name="doc.md",
                    content_type="text/markdown",
                    comuna="Recoleta",
                    rubros=["general"],
                    vigencia_desde=None,
                    vigencia_hasta=None,
                    content_hash="hash",
                    uploaded_by="InnovaRecoleta",
                )

    def test_worker_settings_registers_ingestion_function(self):
        self.assertIn(worker.process_document_ingestion_task, worker.WorkerSettings.functions)
        self.assertIn(worker.process_ai_task, worker.WorkerSettings.functions)


class CleanupOrphanedUploadsJobTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fake_supabase_admin = MagicMock()
        patcher = patch.object(dependencies, "supabase_admin", self.fake_supabase_admin)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_marks_stale_jobs_as_failed_and_removes_their_storage_object(self):
        stale_jobs = [
            {"id": "job-a", "file_name": "a.pdf", "content_hash": "hash-a"},
            {"id": "job-b", "file_name": "b.md", "content_hash": "hash-b"},
        ]

        with patch.object(worker, "get_stale_ingestion_jobs", return_value=stale_jobs) as get_mock, \
             patch.object(worker, "update_ingestion_job") as update_mock:
            await worker.cleanup_orphaned_uploads_job({})

        get_mock.assert_called_once_with(worker.STALE_INGESTION_JOB_MINUTES)
        self.fake_supabase_admin.storage.from_.return_value.remove.assert_any_call(["hash-a/a.pdf"])
        self.fake_supabase_admin.storage.from_.return_value.remove.assert_any_call(["hash-b/b.md"])
        self.assertEqual(update_mock.call_count, 2)
        for call in update_mock.call_args_list:
            self.assertEqual(call.kwargs["status"], "failed")
            self.assertIn("huérfano", call.kwargs["error_message"])

    async def test_does_nothing_when_no_stale_jobs(self):
        with patch.object(worker, "get_stale_ingestion_jobs", return_value=[]), \
             patch.object(worker, "update_ingestion_job") as update_mock:
            await worker.cleanup_orphaned_uploads_job({})

        update_mock.assert_not_called()
        self.fake_supabase_admin.storage.from_.return_value.remove.assert_not_called()

    async def test_storage_failure_does_not_prevent_marking_job_failed(self):
        self.fake_supabase_admin.storage.from_.return_value.remove.side_effect = RuntimeError("not found")
        stale_jobs = [{"id": "job-a", "file_name": "a.pdf", "content_hash": "hash-a"}]

        with patch.object(worker, "get_stale_ingestion_jobs", return_value=stale_jobs), \
             patch.object(worker, "update_ingestion_job") as update_mock:
            await worker.cleanup_orphaned_uploads_job({})

        update_mock.assert_called_once()

    def test_worker_settings_registers_cleanup_cron_job(self):
        jobs_by_coroutine = {job.coroutine: job for job in worker.WorkerSettings.cron_jobs}
        self.assertIn(worker.cleanup_orphaned_uploads_job, jobs_by_coroutine)
        self.assertIn(worker.run_reminders_job, jobs_by_coroutine)


if __name__ == "__main__":
    unittest.main()


class WorkerStartupTests(unittest.IsolatedAsyncioTestCase):
    async def _startup_con(self, db_pool, supabase_admin):
        with patch.object(dependencies, "init_dependencies", AsyncMock()), \
             patch.object(dependencies, "shutdown_dependencies", AsyncMock()) as shutdown_mock, \
             patch.object(dependencies, "db_pool", db_pool), \
             patch.object(dependencies, "supabase_admin", supabase_admin):
            await worker.startup({})
        return shutdown_mock

    async def test_aborts_without_postgres_pool(self):
        with self.assertRaisesRegex(RuntimeError, "Postgres"):
            await self._startup_con(None, MagicMock())

    async def test_aborts_without_supabase_admin(self):
        with self.assertRaisesRegex(RuntimeError, "Supabase"):
            await self._startup_con(MagicMock(), None)

    async def test_starts_with_all_dependencies(self):
        shutdown_mock = await self._startup_con(MagicMock(), MagicMock())
        shutdown_mock.assert_not_awaited()

    def test_worker_uses_configured_queue_name(self):
        self.assertEqual(worker.WorkerSettings.queue_name, worker.ARQ_QUEUE_NAME)
