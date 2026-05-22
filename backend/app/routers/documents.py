from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, EmailStr, Field
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.entities import Document, DocumentOperation, DocumentPermission, User
from app.services.ot import delta_to_plain_text
from app.services.permissions import ROLES, require_document_access, require_document_edit, require_owner, get_document_role
from app.services.redis_service import redis_service

router = APIRouter(prefix="/documents", tags=["documents"])


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


@router.get("", response_model=list[DocumentOut])
def list_documents(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    docs = (
        db.query(Document)
        .outerjoin(DocumentPermission, DocumentPermission.document_id == Document.id)
        .filter(or_(Document.owner_id == current_user.id, DocumentPermission.user_id == current_user.id))
        .order_by(Document.updated_at.desc())
        .all()
    )
    # Remove duplicate rows when a user is both owner and permissioned.
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
def share_document(document_id: str, payload: ShareIn, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
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
    return PermissionOut(id=permission.id, user_id=target.id, email=target.email, username=target.username, role=permission.role)


@router.delete("/{document_id}/permissions/{permission_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_permission(document_id: str, permission_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    require_owner(db, document_id, current_user)
    permission = db.get(DocumentPermission, permission_id)
    if not permission or permission.document_id != document_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Permission not found")
    db.delete(permission)
    db.commit()
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
