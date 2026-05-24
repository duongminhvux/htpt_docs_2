# HTPT Docs - Collaborative Document Editing System

Dự án mô phỏng Google Docs mini cho đồ án **Hệ thống soạn thảo văn bản cộng tác**.

## Tính năng đã có

- Đăng ký, đăng nhập bằng JWT.
- Tạo, mở, đổi tên, xóa tài liệu.
- Giao diện giống Google Docs: top menu, toolbar rich text, trang giấy trắng, sidebar document tabs, nút Share, avatar người đang online.
- Rich text editor bằng Quill: heading, font size, bold, italic, underline, color, align, list, link, image, quote, code block.
- Realtime WebSocket: nhiều người cùng mở một tài liệu và nhận thay đổi gần thời gian thực.
- Presence: hiển thị người dùng đang tham gia chỉnh sửa.
- Cursor event: gửi vị trí con trỏ qua WebSocket, phục vụ mở rộng hiển thị cursor màu.
- Phân quyền tài liệu: owner, editor, viewer.
- PostgreSQL lưu user, document, permission, operation log.
- Redis lưu snapshot document hiện tại để giảm tải database.
- RabbitMQ truyền operation từ WebSocket Gateway sang OT Worker.
- OT Worker xử lý Vector Clock, transform operation và ghi version history.
- Docker Compose đóng gói toàn bộ: frontend, backend, worker, PostgreSQL, Redis, RabbitMQ.

## Kiến trúc

```txt
Client React + Quill
  ↓ WebSocket / HTTP
FastAPI Gateway
  ↓ RabbitMQ operation queue
OT Worker
  ↓
Redis snapshot + PostgreSQL operation log
  ↓ RabbitMQ event fanout
FastAPI Gateway broadcast tới các client đang mở tài liệu
```

## Chạy dự án bằng Docker

```bash
cp .env.example .env
docker compose up --build
```

Mở các URL:

- Frontend: http://localhost:5173
- Backend API docs: http://localhost:8000/docs
- RabbitMQ Management: http://localhost:15672
  - user: `guest`
  - password: `guest`

## Test nhanh realtime

1. Mở http://localhost:5173 ở trình duyệt A.
2. Đăng ký user 1.
3. Tạo tài liệu mới.
4. Mở trình duyệt B hoặc tab ẩn danh.
5. Đăng ký user 2.
6. Quay lại user 1, bấm Share, nhập email user 2, chọn `editor`.
7. User 2 mở tài liệu trong danh sách.
8. Gõ ở một cửa sổ, cửa sổ còn lại sẽ nhận thay đổi realtime.

## Mapping theo yêu cầu PDF

| Yêu cầu | File/Module |
|---|---|
| Tạo và quản lý tài liệu | `backend/app/routers/documents.py`, `frontend/src/pages/DocumentsPage.jsx` |
| Chỉnh sửa cộng tác realtime | `backend/app/routers/websocket.py`, `frontend/src/pages/EditorPage.jsx` |
| Đồng bộ thay đổi | `backend/app/worker/main.py`, `backend/app/services/ot.py` |
| Vector Clock | `backend/app/services/vector_clock.py` |
| Operational Transformation | `backend/app/services/ot.py` |
| Người dùng đang chỉnh sửa | `backend/app/services/connection_manager.py`, `PresenceBar.jsx` |
| Lưu trữ tài liệu | PostgreSQL models trong `backend/app/models/entities.py` |
| Redis snapshot | `backend/app/services/redis_service.py` |
| RabbitMQ queue | `backend/app/services/broker.py` |
| Lịch sử/versioning | `document_operations`, API `/history` |
| Phân quyền owner/editor/viewer | `backend/app/services/permissions.py`, `ShareDialog.jsx` |
| Docker hóa | `docker-compose.yml`, `backend/Dockerfile`, `frontend/Dockerfile` |

## Ghi chú kỹ thuật

- Backend tự tạo bảng khi khởi động bằng SQLAlchemy `Base.metadata.create_all` để dễ chạy đồ án.
- OT implementation tập trung vào Delta ops dạng `insert`, `retain`, `delete`; đủ cho demo rich text và giải thích mô hình đồng bộ trong báo cáo.
- Với production thật, nên thay bằng CRDT/ShareDB/Yjs hoặc thư viện OT đầy đủ hơn, thêm migration Alembic, refresh token, rate limit và cursor rendering chi tiết.

## Lệnh hữu ích

Xóa sạch container và volume database:

```bash
docker compose down -v
```

Chạy lại sau khi sửa code:

```bash
docker compose up --build
```

Xem log backend:

```bash
docker compose logs -f backend
```

Xem log worker:

```bash
docker compose logs -f worker
```