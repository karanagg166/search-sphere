"""Add document_chunks table with pgvector and full-text search.

Revision ID: 009_add_pgvector_chunks
Revises: 008_add_chunk_count
Create Date: 2026-11-04 19:15:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "009_add_pgvector_chunks"
down_revision: Union[str, Sequence[str], None] = "008_add_chunk_count"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if is_postgres:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "document_chunks" not in tables:
        if is_postgres:
            op.execute("""
            CREATE TABLE document_chunks (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                point_id VARCHAR(64) UNIQUE NOT NULL,
                document_id VARCHAR(255) NOT NULL,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                token_count INTEGER NOT NULL DEFAULT 0,
                start_page INTEGER NOT NULL DEFAULT 0,
                end_page INTEGER NOT NULL DEFAULT 0,
                page_numbers JSONB NOT NULL DEFAULT '[]'::jsonb,
                block_types JSONB NOT NULL DEFAULT '[]'::jsonb,
                embedding vector(384) NOT NULL,
                tsv_content tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
                client_id VARCHAR(255),
                tenant_id VARCHAR(255),
                patient_id VARCHAR(255),
                source_system VARCHAR(255),
                collection_id VARCHAR(255),
                owner_subject_id VARCHAR(255),
                document_type VARCHAR(255),
                file_name VARCHAR(255),
                metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """)
            op.execute("""
            CREATE INDEX idx_document_chunks_embedding_hnsw 
            ON document_chunks USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64);
            """)
            op.execute("""
            CREATE INDEX idx_document_chunks_tsv 
            ON document_chunks USING gin (tsv_content);
            """)
            op.execute("""
            CREATE INDEX idx_document_chunks_doc_id 
            ON document_chunks (document_id);
            """)
            op.execute("""
            CREATE INDEX idx_document_chunks_patient_source 
            ON document_chunks (patient_id, source_system);
            """)
            op.execute("""
            CREATE INDEX idx_document_chunks_client_tenant 
            ON document_chunks (client_id, tenant_id);
            """)
        else:
            op.create_table(
                "document_chunks",
                sa.Column("id", sa.String(36), primary_key=True),
                sa.Column("point_id", sa.String(64), unique=True, nullable=False),
                sa.Column("document_id", sa.String(255), nullable=False, index=True),
                sa.Column("chunk_index", sa.Integer(), nullable=False),
                sa.Column("content", sa.Text(), nullable=False),
                sa.Column("token_count", sa.Integer(), nullable=False, default=0),
                sa.Column("start_page", sa.Integer(), nullable=False, default=0),
                sa.Column("end_page", sa.Integer(), nullable=False, default=0),
                sa.Column("page_numbers", sa.JSON(), nullable=False, default=[]),
                sa.Column("block_types", sa.JSON(), nullable=False, default=[]),
                sa.Column("embedding", sa.JSON(), nullable=False),
                sa.Column("tsv_content", sa.Text(), nullable=True),
                sa.Column("client_id", sa.String(255), nullable=True),
                sa.Column("tenant_id", sa.String(255), nullable=True),
                sa.Column("patient_id", sa.String(255), nullable=True),
                sa.Column("source_system", sa.String(255), nullable=True),
                sa.Column("collection_id", sa.String(255), nullable=True),
                sa.Column("owner_subject_id", sa.String(255), nullable=True),
                sa.Column("document_type", sa.String(255), nullable=True),
                sa.Column("file_name", sa.String(255), nullable=True),
                sa.Column("metadata_json", sa.JSON(), nullable=False, default={}),
                sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            )
            op.create_index("idx_document_chunks_doc_id", "document_chunks", ["document_id"])
    elif is_postgres:
        cols = {col["name"] for col in inspector.get_columns("document_chunks")}
        if "tsv_content" not in cols:
            op.execute("ALTER TABLE document_chunks ADD COLUMN tsv_content tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED;")
            op.execute("CREATE INDEX IF NOT EXISTS idx_document_chunks_tsv ON document_chunks USING gin (tsv_content);")
            op.execute("CREATE INDEX IF NOT EXISTS idx_document_chunks_embedding_hnsw ON document_chunks USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "document_chunks" in tables:
        op.drop_table("document_chunks")
