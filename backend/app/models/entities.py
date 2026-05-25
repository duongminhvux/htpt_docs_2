from __future__ import annotations

import uuid
from datetime import datetime
from sqlalchemy import String, Text, DateTime, ForeignKey, Integer, UniqueConstraint, JSON, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


def uuid_str() -> str:
    return str(uuid.uuid4())


def now_utc() -> datetime:
    return datetime.utcnow()


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(80), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)

    owned_documents: Mapped[list["Document"]] = relationship(back_populates="owner")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="Untitled document")
    content_delta: Mapped[dict] = mapped_column(JSON, nullable=False, default=lambda: {"ops": [{"insert": "\n"}]})
    content_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    vector_clock: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, onupdate=now_utc, nullable=False)

    owner: Mapped[User] = relationship(back_populates="owned_documents")
    permissions: Mapped[list["DocumentPermission"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    operations: Mapped[list["DocumentOperation"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    versions: Mapped[list["DocumentVersion"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class DocumentPermission(Base):
    __tablename__ = "document_permissions"
    __table_args__ = (UniqueConstraint("document_id", "user_id", name="uq_document_user_permission"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="viewer")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)

    document: Mapped[Document] = relationship(back_populates="permissions")
    user: Mapped[User] = relationship()


class DocumentOperation(Base):
    __tablename__ = "document_operations"
    __table_args__ = (
        Index("ix_document_operations_doc_version", "document_id", "server_version"),
        Index("ix_document_operations_doc_client_op", "document_id", "client_op_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    client_op_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    operation_delta: Mapped[dict] = mapped_column(JSON, nullable=False)
    transformed_delta: Mapped[dict] = mapped_column(JSON, nullable=False)
    vector_clock: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    base_version: Mapped[int] = mapped_column(Integer, nullable=False)
    server_version: Mapped[int] = mapped_column(Integer, nullable=False)
    causal_relation: Mapped[str] = mapped_column(String(30), nullable=False, default="unknown")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)

    document: Mapped[Document] = relationship(back_populates="operations")
    user: Mapped[User] = relationship()


class DocumentVersion(Base):
    """Snapshot theo version để xem lịch sử, rollback, collaborative undo/redo."""

    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version", name="uq_document_version"),
        Index("ix_document_versions_doc_version", "document_id", "version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_delta: Mapped[dict] = mapped_column(JSON, nullable=False)
    content_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    vector_clock: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    operation_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("document_operations.id", ondelete="SET NULL"), nullable=True)
    action: Mapped[str] = mapped_column(String(30), nullable=False, default="edit")
    source_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)

    document: Mapped[Document] = relationship(back_populates="versions")
    user: Mapped[User] = relationship(foreign_keys=[created_by])
