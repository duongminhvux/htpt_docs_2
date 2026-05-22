from fastapi import HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.entities import Document, DocumentPermission, User

ROLES = {"owner", "editor", "viewer"}
EDIT_ROLES = {"owner", "editor"}


def get_document_role(db: Session, document: Document, user: User) -> str | None:
    if document.owner_id == user.id:
        return "owner"
    permission = (
        db.query(DocumentPermission)
        .filter(DocumentPermission.document_id == document.id, DocumentPermission.user_id == user.id)
        .first()
    )
    return permission.role if permission else None


def require_document_access(db: Session, document_id: str, user: User) -> tuple[Document, str]:
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    role = get_document_role(db, document, user)
    if not role:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access to this document")
    return document, role


def require_document_edit(db: Session, document_id: str, user: User) -> tuple[Document, str]:
    document, role = require_document_access(db, document_id, user)
    if role not in EDIT_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Viewer cannot edit this document")
    return document, role


def require_owner(db: Session, document_id: str, user: User) -> Document:
    document, role = require_document_access(db, document_id, user)
    if role != "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only owner can do this")
    return document
