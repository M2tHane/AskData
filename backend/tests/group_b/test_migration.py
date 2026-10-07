import asyncio
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text

from app.core.audit import DatabaseQueryLogStore, QueryLogRecord, QueryRun
from app.core.config import get_settings
from app.core.db import Database
from app.modules.chat.models import Conversation, Message
from app.modules.datasource.models import DatasourceRecord
from app.modules.embedding.models import EmbeddingItem
from app.modules.embedding.repository import EmbeddingRepository
from app.modules.org.models import Team, User
from app.modules.schema.models import MetaColumn, MetaTable

ROOT = Path(__file__).resolve().parents[2]


def docker(arguments):
    return subprocess.run(["docker", *arguments], check=True, capture_output=True, text=True)


@pytest.mark.external
async def test_postgres_monthly_partition_indexes_payload_and_downgrade(monkeypatch):
    name = f"askdata-group-b-{uuid4().hex[:8]}"
    await asyncio.to_thread(
        docker,
        [
            "run",
            "--rm",
            "-d",
            "--name",
            name,
            "-e",
            "POSTGRES_PASSWORD=group-b-test",
            "-p",
            "127.0.0.1::5432",
            "pgvector/pgvector:pg16",
        ],
    )
    database = None
    try:
        binding = (await asyncio.to_thread(docker, ["port", name, "5432"])).stdout.strip()
        port = binding.rsplit(":", 1)[1]
        url = f"postgresql+asyncpg://postgres:group-b-test@127.0.0.1:{port}/postgres"
        monkeypatch.setenv("ASKDATA_DATABASE_URL", url)
        get_settings.cache_clear()
        database = Database(url)
        for _ in range(100):
            try:
                async with database.session() as session:
                    await session.execute(text("SELECT 1"))
                break
            except Exception:
                await asyncio.sleep(0.1)
        else:
            pytest.fail("Disposable PostgreSQL did not become ready")
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "migrations"))
        await asyncio.to_thread(command.upgrade, config, "0002")
        async with database.transaction() as session:
            session.add(Team(id=1, name="test"))
            await session.flush()
            session.add(User(id=1, username="test", password_hash="fake", role="member", team_id=1))
            session.add(
                DatasourceRecord(
                    id=1,
                    name="fake",
                    type="postgresql",
                    host="fake",
                    port=5432,
                    database="fake",
                    username="readonly",
                    secret_enc="fake",
                    dialect="postgres",
                )
            )
            await session.flush()
            # Old core log shape must survive upgrading to a partitioned table.
            session.add(
                QueryRun(
                    id=9,
                    payload={
                        "user_id": 1,
                        "team_id": 1,
                        "datasource_id": 1,
                        "status": "blocked",
                        "blocked_code": "SQL_OUT_OF_SCOPE",
                    },
                    expires_at=datetime.now(UTC) + timedelta(days=90),
                )
            )
        await asyncio.to_thread(command.upgrade, config, "0004")
        await database.engine.dispose()
        async with database.transaction() as session:
            partitions = await session.scalar(
                text("SELECT count(*) FROM pg_inherits WHERE inhparent='query_run'::regclass")
            )
            assert partitions == 17
            indexes = list(
                await session.execute(
                    text(
                        "SELECT indexname,indexdef FROM pg_indexes WHERE tablename IN "
                        "('embedding_item','query_run','semantic_term','metric')"
                    )
                )
            )
            assert any("USING hnsw" in row.indexdef for row in indexes)
            assert any("USING gin" in row.indexdef for row in indexes)
            assert {
                "ix_query_run_team_id_created_at",
                "ix_query_run_user_id_created_at",
                "ix_query_run_status_created_at",
                "ix_embedding_datasource_kind",
            } <= {row.indexname for row in indexes}
            assert (await session.scalar(select(QueryRun).where(QueryRun.id == 9))).payload[
                "blocked_code"
            ] == "SQL_OUT_OF_SCOPE"
            await DatabaseQueryLogStore(session).append(
                QueryLogRecord(user_id=1, team_id=1, datasource_id=1, status="success")
            )
            session.add(
                MetaTable(
                    id=1,
                    datasource_id=1,
                    schema_name="public",
                    name="orders",
                    synced_at=datetime.now(UTC),
                )
            )
            await session.flush()
            session.add_all(
                [
                    MetaColumn(
                        id=index, table_id=1, name=f"c{index}", data_type="TEXT", ordinal=index
                    )
                    for index in (1, 2)
                ]
            )
            await session.flush()
            session.add_all(
                [
                    EmbeddingItem(
                        datasource_id=1,
                        kind="column",
                        ref_id=index,
                        column_id=index,
                        actor_id=1,
                        text="Order number",
                        embedding=[0.1] * 1024,
                        model_id="1",
                        version=1,
                        dirty=False,
                        revision=1,
                    )
                    for index in (1, 2)
                ]
            )
            await session.flush()
            ranked = await EmbeddingRepository(session).candidates(
                1, ["column"], "1", [0.1] * 1024, "Order", 30, [("column", 2)]
            )
            assert [item.ref_id for item in ranked] == [2]
            assert max(row.id for row in await session.scalars(select(QueryRun))) > 9
            # Feedback references the compound key and is removed with the expiring source log.
            now = datetime.now(UTC)
            reference_id = 2**31 + 17
            session.add(
                QueryRun(
                    id=reference_id,
                    created_at=now,
                    updated_at=now,
                    payload={"user_id": 1, "team_id": 1, "datasource_id": 1, "status": "success"},
                    expires_at=now,
                )
            )
            await session.flush()
            conversation = Conversation(user_id=1, team_id=1, datasource_id=1, title="FK test")
            session.add(conversation)
            await session.flush()
            message = Message(
                conversation_id=conversation.id,
                user_id=1,
                role="assistant",
                content="summary",
                query_run_id=reference_id,
                query_run_created_at=now,
                expires_at=now + timedelta(days=90),
            )
            session.add(message)
            await session.flush()
            await session.execute(
                text(
                    "INSERT INTO feedback (created_at,updated_at,query_run_id,query_run_created_at,"
                    "user_id,team_id,rating,comment,status,snapshot,expires_at) "
                    "VALUES (:now,:now,:query_id,:now,1,1,'up','','open',"
                    "CAST(:snapshot AS json),:now)"
                ),
                {"now": now, "query_id": reference_id, "snapshot": json.dumps({"summary": True})},
            )
            await session.execute(
                text("DELETE FROM query_run WHERE id=:query_id"), {"query_id": reference_id}
            )
            assert await session.scalar(text("SELECT count(*) FROM feedback")) == 0
            await session.refresh(message)
            assert message.query_run_id is None and message.query_run_created_at is None
        await asyncio.to_thread(command.downgrade, config, "0002")
        await database.engine.dispose()
        async with database.session() as session:
            assert len(list(await session.scalars(select(QueryRun)))) == 2
            assert (
                await session.scalar(
                    text("SELECT count(*) FROM pg_inherits WHERE inhparent='query_run'::regclass")
                )
                == 0
            )
    finally:
        if database:
            await database.close()
        get_settings.cache_clear()
        await asyncio.to_thread(docker, ["rm", "-f", name])
