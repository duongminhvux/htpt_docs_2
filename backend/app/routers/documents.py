from __future__ import annotations

import logging
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.entities import Document, DocumentOperation, DocumentPermission, DocumentVersion, User
from app.services.broker import broker
from app.services.connection_manager import manager
from app.services.permissions import require_document_access, require_document_edit, require_owner, get_document_role
from app.services.redis_service import redis_service

router = APIRouter(prefix="/documents", tags=["documents"])
logger = logging.getLogger(__name__)

DEFAULT_DELTA = {"ops": [{"insert": "\n"}]}


class DocumentCreate(BaseModel):
    title: str = Field(default="Untitled document", max_length=255)


class DocumentUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=255)


class DocumentOut(BaseModel):
    id: str
    title: str
    owner_id: str
    role: str
    version: int
    updated_at: datetime
    created_at: datetime


class DocumentDetail(DocumentOut):
    content_delta: dict
    vector_clock: dict


class ShareIn(BaseModel):
    email: EmailStr
    role: str = Field(pattern="^(editor|viewer)$")


class PermissionOut(BaseModel):
    id: str
    user_id: str
    email: str
    username: str
    role: str


class OperationOut(BaseModel):
    id: str
    user_id: str | None
    username: str | None
    operation_delta: dict
    transformed_delta: dict
    vector_clock: dict
    base_version: int
    server_version: int
    causal_relation: str
    created_at: datetime


class VersionOut(BaseModel):
    id: str
    version: int
    action: str
    source_version: int | None
    target_version: int | None
    created_by: str | None
    username: str | None
    content_text: str
    created_at: datetime


class RollbackIn(BaseModel):
    version: int


def doc_to_out(db: Session, doc: Document, user: User) -> DocumentOut:
    role = get_document_role(db, doc, user) or "viewer"
    return DocumentOut(
        id=doc.id,
        title=doc.title,
        owner_id=doc.owner_id,
        role=role,
        version=doc.version,
        updated_at=doc.updated_at,
        created_at=doc.created_at,
    )


def ensure_version_snapshot(
    db: Session,
    doc: Document,
    *,
    user_id: str | None = None,
    operation_id: str | None = None,
    action: str = "edit",
    source_version: int | None = None,
    target_version: int | None = None,
) -> DocumentVersion:
    existing = (
        db.query(DocumentVersion)
        .filter(DocumentVersion.document_id == doc.id, DocumentVersion.version == doc.version)
        .first()
    )
    if existing:
        return existing
    row = DocumentVersion(
        document_id=doc.id,
        version=doc.version,
        content_delta=doc.content_delta,
        content_text=doc.content_text or "",
        vector_clock=doc.vector_clock or {},
        created_by=user_id,
        operation_id=operation_id,
        action=action,
        source_version=source_version,
        target_version=target_version,
    )
    db.add(row)
    return row


async def publish_document_snapshot(doc: Document, action: str, user: User | None = None):
    await redis_service.set_json(
        redis_service.document_key(doc.id),
        {"content_delta": doc.content_delta, "version": doc.version, "vector_clock": doc.vector_clock or {}},
    )
    await broker.publish_event({
        "type": "operation_applied",
        "document_id": doc.id,
        "user_id": user.id if user else None,
        "username": user.username if user else None,
        "client_id": user.id if user else None,
        "client_op_id": f"server-{action}-{doc.version}",
        "operation_id": None,
        "operation_delta": {"ops": [], "action": action},
        "base_version": doc.version - 1,
        "server_version": doc.version,
        "vector_clock": doc.vector_clock or {},
        "causal_relation": action,
        "content_delta": doc.content_delta,
        "content_text": doc.content_text or "",
        "action": action,
    })


async def apply_version_as_new_version(
    db: Session,
    doc: Document,
    target: DocumentVersion,
    user: User,
    action: str,
):
    source_version = doc.version
    new_version = source_version + 1

    doc.content_delta = target.content_delta
    doc.content_text = target.content_text or ""
    doc.vector_clock = {**(doc.vector_clock or {}), "server": int((doc.vector_clock or {}).get("server", 0)) + 1}
    doc.version = new_version

    op_log = DocumentOperation(
        document_id=doc.id,
        user_id=user.id,
        client_op_id=f"server-{action}-{new_version}",
        operation_delta={"ops": [], "action": action, "target_version": target.version},
        transformed_delta={"ops": [], "action": action, "target_version": target.version},
        vector_clock=doc.vector_clock,
        base_version=source_version,
        server_version=new_version,
        causal_relation=action,
    )
    db.add(op_log)
    db.flush()
    ensure_version_snapshot(
        db,
        doc,
        user_id=user.id,
        operation_id=op_log.id,
        action=action,
        source_version=source_version,
        target_version=target.version,
    )
    db.commit()
    db.refresh(doc)
    await publish_document_snapshot(doc, action, user)
    logger.info("[%s] doc=%s user=%s source_v=%s target_v=%s new_v=%s", action.upper(), doc.id, user.id, source_version, target.version, new_version)
    return doc


@router.get("", response_model=list[DocumentOut])
def list_documents(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    docs = (
        db.query(Document)
        .outerjoin(DocumentPermission, DocumentPermission.document_id == Document.id)
        .filter(or_(Document.owner_id == current_user.id, DocumentPermission.user_id == current_user.id))
        .order_by(Document.updated_at.desc())
        .all()
    )
    unique = {doc.id: doc for doc in docs}.values()
    return [doc_to_out(db, doc, current_user) for doc in unique]


@router.post("", response_model=DocumentDetail, status_code=status.HTTP_201_CREATED)
def create_document(payload: DocumentCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    doc = Document(
        title=payload.title.strip() or "Untitled document",
        owner_id=current_user.id,
        content_delta=DEFAULT_DELTA,
        content_text="",
        version=0,
        vector_clock={},
    )
    db.add(doc)
    db.flush()
    ensure_version_snapshot(db, doc, user_id=current_user.id, action="create")
    db.commit()
    db.refresh(doc)
    return DocumentDetail(**doc_to_out(db, doc, current_user).model_dump(), content_delta=doc.content_delta, vector_clock=doc.vector_clock)


@router.get("/{document_id}", response_model=DocumentDetail)
async def get_document(document_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    doc, role = require_document_access(db, document_id, current_user)
    cached = await redis_service.get_json(redis_service.document_key(document_id), default=None)
    content_delta = cached.get("content_delta") if cached else doc.content_delta
    version = cached.get("version", doc.version) if cached else doc.version
    vector_clock = cached.get("vector_clock", doc.vector_clock) if cached else doc.vector_clock
    return DocumentDetail(
        id=doc.id,
        title=doc.title,
        owner_id=doc.owner_id,
        role=role,
        version=version,
        updated_at=doc.updated_at,
        created_at=doc.created_at,
        content_delta=content_delta,
        vector_clock=vector_clock,
    )


@router.patch("/{document_id}", response_model=DocumentOut)
def update_document(document_id: str, payload: DocumentUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    doc, _ = require_document_edit(db, document_id, current_user)
    if payload.title is not None:
        doc.title = payload.title.strip() or "Untitled document"
    db.commit()
    db.refresh(doc)
    return doc_to_out(db, doc, current_user)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    doc = require_owner(db, document_id, current_user)
    await broker.publish_event({"type": "document_deleted", "document_id": document_id, "message": "Document was deleted by owner"})
    db.delete(doc)
    db.commit()
    await redis_service.delete(redis_service.document_key(document_id))
    return None


@router.get("/{document_id}/permissions", response_model=list[PermissionOut])
def list_permissions(document_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    doc = require_owner(db, document_id, current_user)
    rows = (
        db.query(DocumentPermission, User)
        .join(User, User.id == DocumentPermission.user_id)
        .filter(DocumentPermission.document_id == doc.id)
        .order_by(User.email.asc())
        .all()
    )
    return [PermissionOut(id=p.id, user_id=u.id, email=u.email, username=u.username, role=p.role) for p, u in rows]


@router.post("/{document_id}/share", response_model=PermissionOut)
async def share_document(document_id: str, payload: ShareIn, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    doc = require_owner(db, document_id, current_user)
    target = db.query(User).filter(User.email == payload.email.lower().strip()).first()
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User with this email does not exist")
    if target.id == doc.owner_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Owner already has full access")
    permission = (
        db.query(DocumentPermission)
        .filter(DocumentPermission.document_id == doc.id, DocumentPermission.user_id == target.id)
        .first()
    )
    if permission:
        permission.role = payload.role
    else:
        permission = DocumentPermission(document_id=doc.id, user_id=target.id, role=payload.role)
        db.add(permission)
    db.commit()
    db.refresh(permission)
    await broker.publish_event({"type": "role_changed", "document_id": doc.id, "user_id": target.id, "role": permission.role})
    return PermissionOut(id=permission.id, user_id=target.id, email=target.email, username=target.username, role=permission.role)


@router.delete("/{document_id}/permissions/{permission_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_permission(document_id: str, permission_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    require_owner(db, document_id, current_user)
    permission = db.get(DocumentPermission, permission_id)
    if not permission or permission.document_id != document_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Permission not found")
    removed_user_id = permission.user_id
    db.delete(permission)
    db.commit()
    await broker.publish_event({"type": "access_removed", "document_id": document_id, "user_id": removed_user_id, "message": "Owner removed your access"})
    return None


@router.get("/{document_id}/history", response_model=list[OperationOut])
def history(document_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    require_document_access(db, document_id, current_user)
    rows = (
        db.query(DocumentOperation, User)
        .outerjoin(User, User.id == DocumentOperation.user_id)
        .filter(DocumentOperation.document_id == document_id)
        .order_by(DocumentOperation.server_version.desc())
        .limit(100)
        .all()
    )
    return [
        OperationOut(
            id=op.id,
            user_id=op.user_id,
            username=user.username if user else None,
            operation_delta=op.operation_delta,
            transformed_delta=op.transformed_delta,
            vector_clock=op.vector_clock,
            base_version=op.base_version,
            server_version=op.server_version,
            causal_relation=op.causal_relation,
            created_at=op.created_at,
        )
        for op, user in rows
    ]


@router.get("/{document_id}/versions", response_model=list[VersionOut])
def versions(document_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    require_document_access(db, document_id, current_user)
    rows = (
        db.query(DocumentVersion, User)
        .outerjoin(User, User.id == DocumentVersion.created_by)
        .filter(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version.desc())
        .limit(100)
        .all()
    )
    return [
        VersionOut(
            id=v.id,
            version=v.version,
            action=v.action,
            source_version=v.source_version,
            target_version=v.target_version,
            created_by=v.created_by,
            username=u.username if u else None,
            content_text=(v.content_text or "")[:200],
            created_at=v.created_at,
        )
        for v, u in rows
    ]


@router.post("/{document_id}/rollback", response_model=DocumentDetail)
async def rollback(document_id: str, payload: RollbackIn, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    doc, _ = require_document_edit(db, document_id, current_user)
    target = (
        db.query(DocumentVersion)
        .filter(DocumentVersion.document_id == document_id, DocumentVersion.version == payload.version)
        .first()
    )
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")
    doc = await apply_version_as_new_version(db, doc, target, current_user, "rollback")
    return DocumentDetail(**doc_to_out(db, doc, current_user).model_dump(), content_delta=doc.content_delta, vector_clock=doc.vector_clock)


def get_current_version_row(db: Session, document_id: str, version: int) -> DocumentVersion | None:
    return (
        db.query(DocumentVersion)
        .filter(DocumentVersion.document_id == document_id, DocumentVersion.version == version)
        .first()
    )


def is_logical_history_row(row: DocumentVersion) -> bool:
    # undo/redo là snapshot kỹ thuật để broadcast trạng thái mới.
    # Không được lấy chúng làm "bản trước" khi undo tiếp, nếu không sẽ loop 2 bản.
    return row.action not in {"undo", "redo"}


@router.post("/{document_id}/undo", response_model=DocumentDetail)
async def collaborative_undo(document_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    doc, _ = require_document_edit(db, document_id, current_user)

    current_row = get_current_version_row(db, document_id, doc.version)

    # Nếu bản hiện tại là kết quả của undo, lần undo tiếp theo phải đi tiếp từ target_version
    # chứ không được chọn source_version ngay phía trước.
    anchor_version = doc.version
    if current_row and current_row.action == "undo" and current_row.target_version is not None:
        anchor_version = current_row.target_version

    previous = (
        db.query(DocumentVersion)
        .filter(
            DocumentVersion.document_id == document_id,
            DocumentVersion.version < anchor_version,
            DocumentVersion.action.notin_(["undo", "redo"]),
        )
        .order_by(DocumentVersion.version.desc())
        .first()
    )

    if not previous:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nothing to undo")

    doc = await apply_version_as_new_version(db, doc, previous, current_user, "undo")
    return DocumentDetail(**doc_to_out(db, doc, current_user).model_dump(), content_delta=doc.content_delta, vector_clock=doc.vector_clock)


@router.post("/{document_id}/redo", response_model=DocumentDetail)
async def collaborative_redo(document_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    doc, _ = require_document_edit(db, document_id, current_user)

    current_row = get_current_version_row(db, document_id, doc.version)
    if not current_row or current_row.action != "undo" or current_row.target_version is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nothing to redo")

    target = (
        db.query(DocumentVersion)
        .filter(
            DocumentVersion.document_id == document_id,
            DocumentVersion.version > current_row.target_version,
            DocumentVersion.version <= (current_row.source_version or doc.version),
            DocumentVersion.action.notin_(["undo", "redo"]),
        )
        .order_by(DocumentVersion.version.asc())
        .first()
    )

    if not target:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Redo target not found")

    doc = await apply_version_as_new_version(db, doc, target, current_user, "redo")
    return DocumentDetail(**doc_to_out(db, doc, current_user).model_dump(), content_delta=doc.content_delta, vector_clock=doc.vector_clock)
