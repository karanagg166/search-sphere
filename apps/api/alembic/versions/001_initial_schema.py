"""001_initial_schema

Revision ID: 001_initial_schema
Revises: 
Create Date: 2026-10-02 19:25:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '001_initial_schema'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    # 1. Users table
    if "users" not in tables:
        op.create_table(
            "users",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("email", sa.String(length=255), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=True),
            sa.Column("hashed_password", sa.String(length=255), nullable=True),
            sa.Column("avatar_url", sa.String(length=1024), nullable=True),
            sa.Column("auth_provider", sa.String(length=50), nullable=False, server_default="local"),
            sa.Column("provider_id", sa.String(length=255), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index(op.f("ix_users_id"), "users", ["id"], unique=False)
        op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)
        op.create_index(op.f("ix_users_provider_id"), "users", ["provider_id"], unique=False)

    # 2. Documents table
    if "documents" not in tables:
        op.create_table(
            "documents",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("filename", sa.String(length=255), nullable=False),
            sa.Column("storage_key", sa.String(length=512), nullable=False),
            sa.Column("file_url", sa.String(length=1024), nullable=False),
            sa.Column("file_size", sa.BigInteger(), nullable=False),
            sa.Column("mime_type", sa.String(length=100), nullable=False, server_default="application/pdf"),
            sa.Column("status", sa.String(length=50), nullable=False, server_default="uploaded"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index(op.f("ix_documents_id"), "documents", ["id"], unique=False)
        op.create_index(op.f("ix_documents_user_id"), "documents", ["user_id"], unique=False)
        op.create_index(op.f("ix_documents_storage_key"), "documents", ["storage_key"], unique=True)
        op.create_index(op.f("ix_documents_status"), "documents", ["status"], unique=False)

    # 3. Document Contents table
    if "document_contents" not in tables:
        op.create_table(
            "document_contents",
            sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
            sa.Column("document_id", sa.String(length=36), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
            sa.Column("raw_text", sa.Text(), nullable=False),
            sa.Column("character_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index(op.f("ix_document_contents_document_id"), "document_contents", ["document_id"], unique=True)

    # 4. Conversations table
    if "conversations" not in tables:
        op.create_table(
            "conversations",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False, server_default="New Conversation"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index(op.f("ix_conversations_id"), "conversations", ["id"], unique=False)
        op.create_index(op.f("ix_conversations_user_id"), "conversations", ["user_id"], unique=False)

    # 5. Messages table
    if "messages" not in tables:
        op.create_table(
            "messages",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("conversation_id", sa.String(length=36), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("role", sa.String(length=50), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("original_query", sa.Text(), nullable=True),
            sa.Column("retrieval_query", sa.Text(), nullable=True),
            sa.Column("rewritten", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("sources", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index(op.f("ix_messages_id"), "messages", ["id"], unique=False)
        op.create_index(op.f("ix_messages_conversation_id"), "messages", ["conversation_id"], unique=False)

    # 6. Feedbacks table
    if "feedbacks" not in tables:
        op.create_table(
            "feedbacks",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("conversation_id", sa.String(length=36), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True),
            sa.Column("message_id", sa.String(length=36), sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=True),
            sa.Column("rating", sa.Integer(), nullable=False),
            sa.Column("comment", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index(op.f("ix_feedbacks_id"), "feedbacks", ["id"], unique=False)
        op.create_index(op.f("ix_feedbacks_user_id"), "feedbacks", ["user_id"], unique=False)
        op.create_index(op.f("ix_feedbacks_conversation_id"), "feedbacks", ["conversation_id"], unique=False)
        op.create_index(op.f("ix_feedbacks_message_id"), "feedbacks", ["message_id"], unique=False)


def downgrade() -> None:
    op.drop_table("feedbacks")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("document_contents")
    op.drop_table("documents")
    op.drop_table("users")
