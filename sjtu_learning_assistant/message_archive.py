"""消息全库检索：固定 ORM 联合查询，分页发生在筛选之后。"""
from __future__ import annotations

from sqlalchemy import String, cast, func, literal, or_, select, union_all
from sqlalchemy.orm import Session

from .models import Announcement, Assignment, Course, Email, EmailAttachment, UnifiedItem, SyncState
from .course_scope import current_course_ids
import json
from .text_content import content_to_plain_text


def canvas_archive_ids(engine):
    with Session(engine) as session:
        state = session.scalar(select(SyncState).where(SyncState.source == "canvas", SyncState.resource == "course_catalog"))
        if state is not None and state.cursor:
            try:
                payload = json.loads(state.cursor)
                ids = payload["course_ids"]
                if payload.get("version") != 1 or type(ids) is not list or len(ids) > 10000 or any(type(x) is not str for x in ids):
                    return set()
                return set(session.scalars(select(Course.id).where(Course.source_id.in_(ids))))
            except (ValueError, TypeError, KeyError):
                return set()
        return current_course_ids(session)


def archive_statement(kind="all", query="", account=None, course_ids=None):
    statements = []
    for source in ("email", "announcement", "assignment"):
        if kind not in ("all", source):
            continue
        base = select(UnifiedItem.id.label("item_id"), literal(source).label("kind"),
                      UnifiedItem.source_id, UnifiedItem.occurred_at, UnifiedItem.is_read)
        if source == "email":
            title, sender = Email.subject, func.coalesce(Email.sender_name, Email.sender_address, "")
            body = func.coalesce(Email.body_text, Email.body_html, Email.body_preview, "")
            count = select(func.count(EmailAttachment.id)).where(
                EmailAttachment.email_id == Email.id, EmailAttachment.is_inline.is_(False)
            ).correlate(Email).scalar_subquery()
            statement = base.add_columns(title.label("title"), sender.label("source_label"),
                body.label("body"), Email.id.label("record_id"), UnifiedItem.url,
                count.label("attachment_count"), cast(literal(None), String).label("raw"))
            statement = statement.join(Email, Email.id == UnifiedItem.email_id)
            if account is not None:
                # source_id 的账户前缀由 IMAP 同步器生成，模型和网页不能提供账户。
                statement = statement.where(Email.source_id.startswith(account + ":INBOX:", autoescape=True)) if account else statement.where(literal(False))
            fields = [title, sender, Email.sender_address, body]
        else:
            model = Announcement if source == "announcement" else Assignment
            title = model.title if source == "announcement" else model.name
            body = func.coalesce(model.body, "") if source == "announcement" else func.coalesce(model.raw_data["description"].as_string(), "")
            statement = base.add_columns(title.label("title"), Course.name.label("source_label"),
                body.label("body"), model.id.label("record_id"), model.url,
                literal(0).label("attachment_count"), cast(model.raw_data, String).label("raw"))
            foreign = UnifiedItem.announcement_id if source == "announcement" else UnifiedItem.assignment_id
            statement = statement.join(model, model.id == foreign).join(Course, Course.id == model.course_id).where(model.is_active.is_(True))
            if course_ids is not None:
                statement = statement.where(model.course_id.in_(course_ids))
            fields = [title, Course.name, body]
        statement = statement.where(UnifiedItem.item_type == source, UnifiedItem.is_active.is_(True))
        # 空格分词 AND，每个词可命中不同字段；百分号和下划线作为文字。
        for word in query.split():
            pattern = "%" + word.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            statement = statement.where(or_(*(func.lower(field).like(pattern, escape="\\") for field in fields)))
        statements.append(statement)
    return union_all(*statements).subquery()


def search_archive(engine, *, kind="all", query="", offset=0, limit=60, account=None):
    source = archive_statement(kind, query, account, canvas_archive_ids(engine))
    with Session(engine) as session:
        total, unread = session.execute(select(func.count(), func.count().filter(source.c.is_read.is_(False))).select_from(source)).one()
        rows = session.execute(select(source).order_by(source.c.occurred_at.desc().nulls_last(), source.c.kind, source.c.item_id.desc()).offset(offset).limit(limit)).mappings().all()
    items = []
    for row in rows:
        item = dict(row)
        body = content_to_plain_text(item.pop("body")) or ""
        position = min((body.casefold().find(word.casefold()) for word in query.split() if word.casefold() in body.casefold()), default=0)
        start = max(0, position - 40)
        item["preview"] = ("…" if start else "") + body[start:start + 180].replace("\n", " ")
        item["is_unread"] = not item.pop("is_read")
        items.append(item)
    return {"items": items, "total": total, "unread": unread,
            "next_offset": offset + len(items) if offset + len(items) < total else None}
