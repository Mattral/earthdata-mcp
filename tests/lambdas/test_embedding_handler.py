"""Tests for the embedding lambda handler."""

import json
from unittest.mock import MagicMock, patch

import pytest

from lambdas.embedding.handler import (
    handle_delete,
    handle_update,
    handler,
    process_message,
)
from lambdas.enrichment.embed import embed_chunks
from models.cmr import ConceptMessage, ConceptType, EmbeddingChunk


@pytest.fixture(autouse=True)
def set_env(monkeypatch):
    """Set required environment variables."""
    monkeypatch.setenv("CMR_URL", "https://cmr.earthdata.nasa.gov")
    monkeypatch.setenv("DATABASE_SECRET_ID", "test-secret")
    monkeypatch.setenv("EMBEDDINGS_TABLE", "concept_embeddings")
    monkeypatch.setenv(
        "ENRICHMENT_STATE_MACHINE_ARN",
        "arn:aws:states:us-east-1:123456789:stateMachine:test-enrichment",
    )


class TestHandleUpdate:
    """Tests for handle_update function — starts Step Function execution."""

    def test_starts_step_function_execution(self):
        """Test that handle_update starts a Step Function execution."""
        mock_sfn = MagicMock()
        mock_sfn.start_execution.return_value = {
            "executionArn": "arn:aws:states:us-east-1:123456789:execution:test:test-exec"
        }

        message = ConceptMessage(
            action="concept-update",
            concept_type="collection",
            concept_id="C1234-PROV",
            revision_id="1",
        )

        with patch("util.sfn._client", mock_sfn):
            result = handle_update(message)

        assert result["status"] == "enrichment_started"
        assert result["concept_id"] == "C1234-PROV"
        assert "execution_arn" in result
        mock_sfn.start_execution.assert_called_once()

        # Verify Step Function input
        call_kwargs = mock_sfn.start_execution.call_args.kwargs
        sfn_input = json.loads(call_kwargs["input"])
        assert sfn_input["concept_id"] == "C1234-PROV"
        assert sfn_input["revision_id"] == 1
        assert sfn_input["concept_type"] == "collection"

    def test_uses_correct_state_machine_arn(self):
        """Test that the correct state machine ARN is used."""
        mock_sfn = MagicMock()
        mock_sfn.start_execution.return_value = {
            "executionArn": "arn:aws:states:us-east-1:123456789:execution:test:test-exec"
        }

        message = ConceptMessage(
            action="concept-update",
            concept_type="collection",
            concept_id="C1234-PROV",
            revision_id="1",
        )

        with patch("util.sfn._client", mock_sfn):
            handle_update(message)

        call_kwargs = mock_sfn.start_execution.call_args.kwargs
        assert call_kwargs["stateMachineArn"] == (
            "arn:aws:states:us-east-1:123456789:stateMachine:test-enrichment"
        )

    def test_raises_when_env_var_missing(self, monkeypatch):
        """Test that missing ENRICHMENT_STATE_MACHINE_ARN raises ValueError."""
        monkeypatch.delenv("ENRICHMENT_STATE_MACHINE_ARN")

        message = ConceptMessage(
            action="concept-update",
            concept_type="collection",
            concept_id="C1234-PROV",
            revision_id="1",
        )

        with pytest.raises(ValueError, match="ENRICHMENT_STATE_MACHINE_ARN"):
            handle_update(message)


class TestHandleDelete:
    """Tests for handle_delete function."""

    def test_deletes_embeddings_and_associations(self):
        """Test that delete removes chunks and associations."""
        mock_repo = MagicMock()
        mock_repo.delete_chunks.return_value = 3
        mock_repo.delete_associations.return_value = 2
        mock_repo.delete_kms_associations.return_value = 5
        mock_repo.delete_collection.return_value = True

        message = ConceptMessage(
            action="concept-delete",
            concept_type="collection",
            concept_id="C1234-PROV",
            revision_id="1",
        )

        handle_delete(message, mock_repo)

        mock_repo.delete_chunks.assert_called_once_with("C1234-PROV")
        mock_repo.delete_associations.assert_called_once_with("C1234-PROV")
        mock_repo.delete_kms_associations.assert_called_once_with("C1234-PROV")
        mock_repo.delete_collection.assert_called_once_with("C1234-PROV")

    def test_delete_collection_only_for_collections(self):
        """Test that delete_collection is only called for collection type."""
        mock_repo = MagicMock()
        mock_repo.delete_chunks.return_value = 1
        mock_repo.delete_associations.return_value = 0
        mock_repo.delete_kms_associations.return_value = 0

        message = ConceptMessage(
            action="concept-delete",
            concept_type="variable",
            concept_id="V1234-PROV",
            revision_id="1",
        )

        handle_delete(message, mock_repo)

        mock_repo.delete_chunks.assert_called_once()
        mock_repo.delete_collection.assert_not_called()


class TestProcessMessage:
    """Tests for process_message routing."""

    def test_routes_update_to_step_function(self):
        """Test that update messages start a Step Function execution."""
        mock_sfn = MagicMock()
        mock_sfn.start_execution.return_value = {
            "executionArn": "arn:aws:states:us-east-1:123456789:execution:test:test-exec"
        }
        mock_repo = MagicMock()

        record = {
            "messageId": "msg-1",
            "body": json.dumps(
                {
                    "action": "concept-update",
                    "concept-type": "collection",
                    "concept-id": "C1234-PROV",
                    "revision-id": 1,
                }
            ),
        }

        with patch("util.sfn._client", mock_sfn):
            process_message(record, mock_repo)

        mock_sfn.start_execution.assert_called_once()

    def test_routes_delete_to_datastore(self):
        """Test that delete messages go to the datastore for cleanup."""
        mock_repo = MagicMock()
        mock_repo.delete_chunks.return_value = 0
        mock_repo.delete_associations.return_value = 0
        mock_repo.delete_kms_associations.return_value = 0

        record = {
            "messageId": "msg-1",
            "body": json.dumps(
                {
                    "action": "concept-delete",
                    "concept-type": "variable",
                    "concept-id": "V1234-PROV",
                    "revision-id": 1,
                }
            ),
        }

        process_message(record, mock_repo)

        mock_repo.delete_chunks.assert_called_once_with("V1234-PROV")


class TestHandler:
    """Tests for the Lambda handler function."""

    def test_handler_processes_update_via_step_function(self):
        """Test that handler routes updates to Step Function."""
        mock_sfn = MagicMock()
        mock_sfn.start_execution.return_value = {
            "executionArn": "arn:aws:states:us-east-1:123456789:execution:test:test-exec"
        }

        event = {
            "Records": [
                {
                    "messageId": "msg-1",
                    "body": json.dumps(
                        {
                            "action": "concept-update",
                            "concept-type": "collection",
                            "concept-id": "C1234-PROV",
                            "revision-id": 1,
                        }
                    ),
                }
            ]
        }

        with (
            patch("util.sfn._client", mock_sfn),
            patch("lambdas.embedding.handler.get_datastore") as mock_get_repo,
            patch("lambdas.embedding.handler.flush_langfuse"),
        ):
            mock_get_repo.return_value = MagicMock()
            result = handler(event, None)

        assert not result["batchItemFailures"]
        mock_sfn.start_execution.assert_called_once()

    def test_handler_processes_delete_via_datastore(self):
        """Test that handler routes deletes to datastore."""
        event = {
            "Records": [
                {
                    "messageId": "msg-1",
                    "body": json.dumps(
                        {
                            "action": "concept-delete",
                            "concept-type": "collection",
                            "concept-id": "C1234-PROV",
                            "revision-id": 1,
                        }
                    ),
                }
            ]
        }

        with patch("lambdas.embedding.handler.get_datastore") as mock_get_repo:
            mock_repo = MagicMock()
            mock_repo.delete_chunks.return_value = 1
            mock_repo.delete_associations.return_value = 0
            mock_repo.delete_kms_associations.return_value = 0
            mock_repo.delete_collection.return_value = True
            mock_get_repo.return_value = mock_repo
            with patch("lambdas.embedding.handler.flush_langfuse"):
                result = handler(event, None)

        assert not result["batchItemFailures"]
        mock_repo.delete_chunks.assert_called_once()

    def test_handler_reports_failures(self):
        """Test that handler reports message failures."""
        event = {
            "Records": [
                {
                    "messageId": "msg-1",
                    "body": "not valid json",
                }
            ]
        }

        with patch("lambdas.embedding.handler.get_datastore") as mock_get_repo:
            mock_get_repo.return_value = MagicMock()
            with patch("lambdas.embedding.handler.flush_langfuse"):
                result = handler(event, None)

        assert len(result["batchItemFailures"]) == 1
        assert result["batchItemFailures"][0]["itemIdentifier"] == "msg-1"

    def test_handler_continues_on_partial_failure(self):
        """Test that handler continues processing after a failure."""
        mock_sfn = MagicMock()
        mock_sfn.start_execution.return_value = {
            "executionArn": "arn:aws:states:us-east-1:123456789:execution:test:test-exec"
        }

        event = {
            "Records": [
                {
                    "messageId": "msg-1",
                    "body": "not valid json",
                },
                {
                    "messageId": "msg-2",
                    "body": json.dumps(
                        {
                            "action": "concept-update",
                            "concept-type": "collection",
                            "concept-id": "C5678-PROV",
                            "revision-id": 1,
                        }
                    ),
                },
            ]
        }

        with (
            patch("util.sfn._client", mock_sfn),
            patch("lambdas.embedding.handler.get_datastore") as mock_get_repo,
            patch("lambdas.embedding.handler.flush_langfuse"),
        ):
            mock_get_repo.return_value = MagicMock()
            result = handler(event, None)

        # First message should have failed, second should succeed
        assert len(result["batchItemFailures"]) == 1
        assert result["batchItemFailures"][0]["itemIdentifier"] == "msg-1"
        mock_sfn.start_execution.assert_called_once()


class TestUpsertChunksDiff:
    """Tests for diff-based upsert_chunks in PostgresEmbeddingDatastore."""

    def _make_datastore(self, existing_rows):
        """Create a mock datastore that simulates diff-based upsert_chunks logic."""
        from util.datastores.postgres import PostgresEmbeddingDatastore

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = existing_rows
        mock_cursor.__enter__ = lambda s: s
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.transaction.return_value = MagicMock(
            __enter__=MagicMock(), __exit__=MagicMock(return_value=False)
        )

        with patch("util.datastores.postgres.get_db_connection", return_value=mock_conn):
            ds = PostgresEmbeddingDatastore()

        return ds, mock_cursor

    def test_skips_unchanged_chunks(self):
        """Unchanged text_content should not trigger UPDATE or INSERT."""
        ds, cur = self._make_datastore([("title", "Hello World")])
        result = ds.upsert_chunks("collection", "C1", [("title", "Hello World", [0.1])])
        assert result == 1
        # Only the SELECT should have been called, no DELETE/UPDATE/INSERT for unchanged
        executed_sqls = [c.args[0].strip() for c in cur.execute.call_args_list]
        assert not any("UPDATE" in sql for sql in executed_sqls)
        assert not any("INSERT" in sql for sql in executed_sqls)

    def test_updates_changed_chunks(self):
        """Changed text_content should trigger UPDATE."""
        ds, cur = self._make_datastore([("title", "Old text")])
        result = ds.upsert_chunks("collection", "C1", [("title", "New text", [0.2])])
        assert result == 1
        executed_sqls = [c.args[0].strip() for c in cur.execute.call_args_list]
        assert any("UPDATE" in sql for sql in executed_sqls)

    def test_inserts_new_chunks(self):
        """New attributes should trigger INSERT."""
        ds, cur = self._make_datastore([])
        result = ds.upsert_chunks("collection", "C1", [("abstract", "Some text", [0.3])])
        assert result == 1
        executed_sqls = [c.args[0].strip() for c in cur.execute.call_args_list]
        assert any("INSERT" in sql for sql in executed_sqls)

    def test_deletes_stale_chunks(self):
        """Attributes in DB but not in new set should be deleted."""
        ds, cur = self._make_datastore([("title", "Hello"), ("stale_attr", "Old")])
        result = ds.upsert_chunks("collection", "C1", [("title", "Hello", [0.1])])
        assert result == 1
        executed_sqls = [c.args[0].strip() for c in cur.execute.call_args_list]
        assert any("DELETE" in sql and "ANY" in sql for sql in executed_sqls)

    def test_empty_chunks_cleans_up_stale(self):
        """Empty chunks list should delete all existing chunks."""
        ds, cur = self._make_datastore([("title", "Hello")])
        result = ds.upsert_chunks("collection", "C1", [])
        assert result == 0
        executed_sqls = [c.args[0].strip() for c in cur.execute.call_args_list]
        assert any("DELETE" in sql and "ANY" in sql for sql in executed_sqls)


class TestUpsertKmsAssociationsDiff:
    """Tests for diff-based upsert_kms_associations."""

    def _make_datastore(self, existing_rows):
        """Create a mock datastore with existing KMS associations."""
        from util.datastores.postgres import PostgresEmbeddingDatastore

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = existing_rows
        mock_cursor.rowcount = 1
        mock_cursor.__enter__ = lambda s: s
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.transaction.return_value = MagicMock(
            __enter__=MagicMock(), __exit__=MagicMock(return_value=False)
        )

        with patch("util.datastores.postgres.get_db_connection", return_value=mock_conn):
            ds = PostgresEmbeddingDatastore()

        return ds, mock_cursor

    def test_empty_list_deletes_all_stale(self):
        """Empty kms_refs should delete all existing KMS associations."""
        # existing_rows format matches SELECT: (right_id, right_type)
        ds, cur = self._make_datastore([("uuid-1", "instruments")])
        ds.upsert_kms_associations("collection", "C1", [])
        executed_sqls = [c.args[0].strip() for c in cur.execute.call_args_list]
        assert any("DELETE" in sql for sql in executed_sqls)

    def test_skips_unchanged_associations(self):
        """Existing associations that are still valid should not be re-inserted."""
        # existing_rows format matches SELECT: (right_id, right_type)
        # kms_refs format is (kms_uuid, scheme) which matches (right_id, right_type)
        ds, cur = self._make_datastore([("uuid-1", "instruments")])
        ds.upsert_kms_associations("collection", "C1", [("uuid-1", "instruments")])
        executed_sqls = [c.args[0].strip() for c in cur.execute.call_args_list]
        assert not any("INSERT" in sql for sql in executed_sqls)
        assert not any("DELETE" in sql and "right_id" in sql for sql in executed_sqls)


class TestEmbedChunksExistingReuse:
    """Tests for embed_chunks reusing existing embeddings."""

    def test_skips_embedder_for_matching_text(self):
        """embed_chunks should reuse existing embedding when text matches."""
        mock_embedder = MagicMock()
        existing = {"title": ("Hello World", [0.1, 0.2, 0.3])}
        chunks = [
            EmbeddingChunk(
                concept_type=ConceptType.COLLECTION,
                concept_id="C1",
                attribute="title",
                text_content="Hello World",
            )
        ]

        results = embed_chunks(chunks, mock_embedder, existing_chunks=existing)

        mock_embedder.generate.assert_not_called()
        assert len(results) == 1
        assert results[0] == ("title", "Hello World", [0.1, 0.2, 0.3])

    def test_calls_embedder_for_changed_text(self):
        """embed_chunks should call embedder when text has changed."""
        mock_embedder = MagicMock()
        mock_embedder.generate.return_value = [0.4, 0.5, 0.6]
        existing = {"title": ("Old text", [0.1, 0.2, 0.3])}
        chunks = [
            EmbeddingChunk(
                concept_type=ConceptType.COLLECTION,
                concept_id="C1",
                attribute="title",
                text_content="New text",
            )
        ]

        results = embed_chunks(chunks, mock_embedder, existing_chunks=existing)

        mock_embedder.generate.assert_called_once()
        assert results[0] == ("title", "New text", [0.4, 0.5, 0.6])

    def test_calls_embedder_for_new_attribute(self):
        """embed_chunks should call embedder for attributes not in existing."""
        mock_embedder = MagicMock()
        mock_embedder.generate.return_value = [0.7, 0.8, 0.9]
        existing = {}
        chunks = [
            EmbeddingChunk(
                concept_type=ConceptType.COLLECTION,
                concept_id="C1",
                attribute="abstract",
                text_content="Some abstract",
            )
        ]

        results = embed_chunks(chunks, mock_embedder, existing_chunks=existing)

        mock_embedder.generate.assert_called_once()
        assert results[0] == ("abstract", "Some abstract", [0.7, 0.8, 0.9])
