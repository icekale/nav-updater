from __future__ import annotations

import shutil
import uuid
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from .auth import (
    csrf_token,
    current_user,
    ensure_initial_admin,
    hash_password,
    require_admin,
    require_csrf,
    verify_password,
)
from .catalog import import_catalog
from .config import Settings, ensure_data_dir, get_settings
from .db import SessionLocal, get_session
from .domain.matching import parse_catalog_csv
from .jobs.processor import ALL_METRICS, process_run
from .jobs.review import (
    METRIC_FIELDS,
    ManualReviewError,
    formatted_metric_values,
    save_manual_review,
)
from .jobs.service import create_run, resolve_item
from .meetings import MeetingImportError, import_meetings
from .models import AuditLog, Meeting, Product, RunItem, UpdateRun, User

ATTENDANCE_OPTIONS = (
    ("unplanned", "未安排"),
    ("planned", "计划参会"),
    ("attended", "已参会"),
    ("absent", "未参会"),
)
ATTENDANCE_LABELS = dict(ATTENDANCE_OPTIONS)


def _parse_filter_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def create_app(
    settings: Settings | None = None,
    session_factory: sessionmaker | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    session_factory = session_factory or SessionLocal
    app = FastAPI(title="投研净值更新工具", version="0.1.0")
    app.state.settings = settings
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, max_age=8 * 60 * 60)
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
    app.mount(
        "/static",
        StaticFiles(directory=str(Path(__file__).parent / "static")),
        name="static",
    )

    def session_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = session_override

    @app.on_event("startup")
    def startup() -> None:
        ensure_data_dir(settings)
        session = session_factory()
        try:
            ensure_initial_admin(
                session,
                settings.initial_admin_username,
                settings.initial_admin_password,
            )
        finally:
            session.close()

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> RedirectResponse:
        return RedirectResponse("/updates", status_code=status.HTTP_303_SEE_OTHER)

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"csrf_token": csrf_token(request), "error": None},
        )

    @app.post("/login", response_class=HTMLResponse)
    def login(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        token: str = Form(...),
        session: Session = Depends(get_session),
    ):
        require_csrf(request, token)
        user = session.scalar(select(User).where(User.username == username.strip()))
        if user is None or not user.is_active or not verify_password(password, user.password_hash):
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={"csrf_token": csrf_token(request), "error": "账号或密码错误"},
                status_code=401,
            )
        request.session["user_id"] = user.id
        return RedirectResponse("/updates", status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/logout")
    def logout(request: Request, token: str = Form(...), user: User = Depends(current_user)):
        require_csrf(request, token)
        request.session.clear()
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    @app.get("/updates", response_class=HTMLResponse)
    def updates_page(
        request: Request,
        user: User = Depends(current_user),
        session: Session = Depends(get_session),
    ):
        runs = session.scalars(select(UpdateRun).order_by(UpdateRun.id.desc()).limit(50)).all()
        return templates.TemplateResponse(
            request=request,
            name="updates.html",
            context={"user": user, "runs": runs, "csrf_token": csrf_token(request)},
        )

    def meeting_list_response(
        request: Request,
        user: User,
        session: Session,
        *,
        q: str = "",
        date_from: str = "",
        date_to: str = "",
        level: str = "",
        company: str = "",
        industry: str = "",
        error: str | None = None,
        notice: str | None = None,
        status_code: int = 200,
    ) -> HTMLResponse:
        statement = select(Meeting)
        if q.strip():
            term = f"%{q.strip()}%"
            statement = statement.where(
                Meeting.title.ilike(term)
                | Meeting.level.ilike(term)
                | Meeting.market_impact.ilike(term)
                | Meeting.research_mapping.ilike(term)
            )
        if level:
            statement = statement.where(Meeting.level == level)
        if company.strip():
            statement = statement.where(Meeting.company_tags.ilike(f"%{company.strip()}%"))
        if industry.strip():
            statement = statement.where(Meeting.industry_tags.ilike(f"%{industry.strip()}%"))
        start = _parse_filter_date(date_from)
        end = _parse_filter_date(date_to)
        if start:
            statement = statement.where(Meeting.date_end >= start)
        if end:
            statement = statement.where(Meeting.date_start <= end)
        ordered = statement.order_by(Meeting.date_start.desc(), Meeting.id.desc())
        meetings = session.scalars(ordered).all()
        levels = session.scalars(select(Meeting.level).distinct().order_by(Meeting.level)).all()
        return templates.TemplateResponse(
            request=request,
            name="meetings.html",
            context={
                "user": user,
                "meetings": meetings,
                "levels": levels,
                "filters": {
                    "q": q,
                    "date_from": date_from,
                    "date_to": date_to,
                    "level": level,
                    "company": company,
                    "industry": industry,
                },
                "attendance_labels": ATTENDANCE_LABELS,
                "csrf_token": csrf_token(request),
                "error": error,
                "notice": notice,
            },
            status_code=status_code,
        )

    @app.get("/meetings", response_class=HTMLResponse)
    def meetings_page(
        request: Request,
        q: str = "",
        date_from: str = "",
        date_to: str = "",
        level: str = "",
        company: str = "",
        industry: str = "",
        notice: str | None = None,
        user: User = Depends(current_user),
        session: Session = Depends(get_session),
    ):
        return meeting_list_response(
            request,
            user,
            session,
            q=q,
            date_from=date_from,
            date_to=date_to,
            level=level,
            company=company,
            industry=industry,
            notice=notice,
        )

    @app.post("/meetings/import", response_class=HTMLResponse)
    async def meeting_import(
        request: Request,
        token: str = Form(...),
        workbook: UploadFile | None = File(None),
        user: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ):
        require_csrf(request, token)
        if workbook is None or not workbook.filename:
            return meeting_list_response(
                request, user, session, error="请上传会议 Excel", status_code=422
            )
        filename = Path(workbook.filename).name
        if Path(filename).suffix.lower() != ".xlsx":
            return meeting_list_response(
                request, user, session, error="仅支持 .xlsx 文件", status_code=422
            )
        import_dir = ensure_data_dir(app.state.settings) / "meeting-imports"
        import_dir.mkdir(parents=True, exist_ok=True)
        temporary_path = import_dir / f"{uuid.uuid4().hex}.xlsx"
        try:
            with temporary_path.open("wb") as handle:
                shutil.copyfileobj(workbook.file, handle)
            result = import_meetings(session, temporary_path)
        except MeetingImportError as exc:
            return meeting_list_response(request, user, session, error=str(exc), status_code=422)
        finally:
            temporary_path.unlink(missing_ok=True)
        session.add(
            AuditLog(
                actor_id=user.id,
                action="import",
                object_type="meeting_workbook",
                object_id="meeting_workbook",
                context={
                    "filename": filename,
                    "created": result.created,
                    "updated": result.updated,
                    "skipped": result.skipped,
                },
            )
        )
        session.commit()
        notice = urlencode({"notice": f"导入 {result.created} 条，更新 {result.updated} 条"})
        return RedirectResponse(f"/meetings?{notice}", status_code=status.HTTP_303_SEE_OTHER)

    def meeting_detail_response(
        request: Request,
        user: User,
        meeting: Meeting,
        *,
        error: str | None = None,
        status_code: int = 200,
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="meeting_detail.html",
            context={
                "user": user,
                "meeting": meeting,
                "attendance_options": ATTENDANCE_OPTIONS,
                "attendance_labels": ATTENDANCE_LABELS,
                "csrf_token": csrf_token(request),
                "error": error,
            },
            status_code=status_code,
        )

    @app.get("/meetings/{meeting_id}", response_class=HTMLResponse)
    def meeting_detail(
        meeting_id: int,
        request: Request,
        user: User = Depends(current_user),
        session: Session = Depends(get_session),
    ):
        meeting = session.get(Meeting, meeting_id)
        if meeting is None:
            return HTMLResponse("会议不存在", status_code=404)
        return meeting_detail_response(request, user, meeting)

    @app.post("/meetings/{meeting_id}/record", response_class=HTMLResponse)
    def save_meeting_record(
        meeting_id: int,
        request: Request,
        token: str = Form(...),
        company_tags: str = Form(""),
        industry_tags: str = Form(""),
        attendance_status: str = Form("unplanned"),
        minutes: str = Form(""),
        todo: str = Form(""),
        conclusion: str = Form(""),
        user: User = Depends(current_user),
        session: Session = Depends(get_session),
    ):
        require_csrf(request, token)
        meeting = session.get(Meeting, meeting_id)
        if meeting is None:
            return HTMLResponse("会议不存在", status_code=404)
        if attendance_status not in ATTENDANCE_LABELS:
            return meeting_detail_response(
                request,
                user,
                meeting,
                error="参会状态无效",
                status_code=422,
            )
        meeting.company_tags = company_tags.strip()
        meeting.industry_tags = industry_tags.strip()
        meeting.attendance_status = attendance_status
        meeting.minutes = minutes.strip()
        meeting.todo = todo.strip()
        meeting.conclusion = conclusion.strip()
        session.add(
            AuditLog(
                actor_id=user.id,
                action="update",
                object_type="meeting",
                object_id=str(meeting.id),
                context={
                    "fields": [
                        "company_tags",
                        "industry_tags",
                        "attendance_status",
                        "minutes",
                        "todo",
                        "conclusion",
                    ]
                },
            )
        )
        session.commit()
        return RedirectResponse(f"/meetings/{meeting.id}", status_code=status.HTTP_303_SEE_OTHER)

    @app.get("/updates/new", response_class=HTMLResponse)
    def new_update_page(request: Request, user: User = Depends(current_user)):
        return templates.TemplateResponse(
            request=request,
            name="new_update.html",
            context={
                "user": user,
                "csrf_token": csrf_token(request),
                "today": date.today().isoformat(),
            },
        )

    @app.post("/updates/new")
    async def new_update(
        request: Request,
        token: str = Form(...),
        cutoff_date: str = Form(...),
        workbook: UploadFile | None = File(None),
        images: list[UploadFile] = File(default=[]),
        user: User = Depends(current_user),
        session: Session = Depends(get_session),
    ):
        require_csrf(request, token)
        if workbook is None or not workbook.filename:
            return RedirectResponse("/updates/new?error=请上传Excel", status_code=303)
        try:
            cutoff = date.fromisoformat(cutoff_date)
        except ValueError:
            return RedirectResponse("/updates/new?error=截止日期无效", status_code=303)
        run_dir = ensure_data_dir(app.state.settings) / "runs" / uuid.uuid4().hex
        run_dir.mkdir(parents=True, exist_ok=False)
        workbook_path = run_dir / Path(workbook.filename).name
        with workbook_path.open("wb") as handle:
            shutil.copyfileobj(workbook.file, handle)
        image_paths: list[Path] = []
        for image in images:
            if not image.filename:
                continue
            image_path = run_dir / Path(image.filename).name
            with image_path.open("wb") as handle:
                shutil.copyfileobj(image.file, handle)
            image_paths.append(image_path)
        run = create_run(
            session,
            operator_id=user.id,
            cutoff_date=cutoff,
            workbook_path=workbook_path,
            image_paths=image_paths,
        )
        session.add(
            AuditLog(
                actor_id=user.id,
                action="create",
                object_type="update_run",
                object_id=str(run.id),
            )
        )
        session.commit()
        return RedirectResponse(f"/updates/{run.id}/preview", status_code=303)

    @app.get("/updates/{run_id}/preview", response_class=HTMLResponse)
    def preview_update(
        run_id: int,
        request: Request,
        user: User = Depends(current_user),
        session: Session = Depends(get_session),
    ):
        run = session.get(UpdateRun, run_id)
        if run is None:
            return HTMLResponse("批次不存在", status_code=404)
        items = list(run.items)
        return templates.TemplateResponse(
            request=request,
            name="preview.html",
            context={
                "user": user,
                "run": run,
                "items": items,
                "csrf_token": csrf_token(request),
            },
        )

    def review_response(
        request: Request,
        run: UpdateRun,
        session: Session,
        user: User,
        *,
        error: str | None = None,
        status_code: int = 200,
    ) -> HTMLResponse:
        products = session.scalars(
            select(Product).where(Product.is_active.is_(True)).order_by(Product.product_code)
        ).all()
        review_rows = [
            {"item": item, "metric_values": formatted_metric_values(item)} for item in run.items
        ]
        return templates.TemplateResponse(
            request=request,
            name="review.html",
            context={
                "user": user,
                "run": run,
                "products": products,
                "review_rows": review_rows,
                "metric_fields": METRIC_FIELDS,
                "csrf_token": csrf_token(request),
                "error": error,
            },
            status_code=status_code,
        )

    @app.get("/updates/{run_id}/review", response_class=HTMLResponse)
    def review_update(
        run_id: int,
        request: Request,
        user: User = Depends(current_user),
        session: Session = Depends(get_session),
    ):
        run = session.get(UpdateRun, run_id)
        if run is None:
            return HTMLResponse("批次不存在", status_code=404)
        return review_response(request, run, session, user)

    @app.post("/updates/{run_id}/items/{item_id}/review", response_class=HTMLResponse)
    async def save_review(
        run_id: int,
        item_id: int,
        request: Request,
        token: str = Form(...),
        product_id: int = Form(...),
        review_note: str = Form(...),
        user: User = Depends(current_user),
        session: Session = Depends(get_session),
    ):
        require_csrf(request, token)
        run = session.get(UpdateRun, run_id)
        item = session.get(RunItem, item_id)
        if run is None or item is None or item.run_id != run_id:
            return HTMLResponse("条目不存在", status_code=404)
        product = session.scalar(
            select(Product).where(Product.id == product_id, Product.is_active.is_(True))
        )
        if product is None:
            return review_response(
                request,
                run,
                session,
                user,
                error="请选择有效产品",
                status_code=422,
            )
        form = await request.form()
        inputs = {field.name: form.get(field.name, "") for field in METRIC_FIELDS}
        try:
            reviewed = save_manual_review(
                session,
                item=item,
                product=product,
                inputs=inputs,
                note=review_note,
            )
        except ManualReviewError as exc:
            return review_response(request, run, session, user, error=str(exc), status_code=422)
        session.add(
            AuditLog(
                actor_id=user.id,
                action="manual_review",
                object_type="run_item",
                object_id=str(reviewed.id),
                context={
                    "product_code": product.product_code,
                    "metrics": reviewed.metric_values,
                    "note": review_note.strip(),
                },
            )
        )
        session.commit()
        return RedirectResponse(f"/updates/{run_id}/review", status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/updates/{run_id}/process")
    def process_update(
        run_id: int,
        request: Request,
        token: str = Form(...),
        user: User = Depends(current_user),
        session: Session = Depends(get_session),
    ):
        require_csrf(request, token)
        run = session.get(UpdateRun, run_id)
        if run is None:
            return HTMLResponse("批次不存在", status_code=404)
        process_run(session, run_id)
        session.add(
            AuditLog(
                actor_id=user.id, action="process", object_type="update_run", object_id=str(run_id)
            )
        )
        session.commit()
        return RedirectResponse(f"/updates/{run_id}/preview", status_code=303)

    @app.post("/updates/{run_id}/items/{item_id}/resolve")
    def resolve_update_item(
        run_id: int,
        item_id: int,
        request: Request,
        action: str = Form(...),
        token: str = Form(...),
        user: User = Depends(current_user),
        session: Session = Depends(get_session),
    ):
        require_csrf(request, token)
        item = session.get(RunItem, item_id)
        if item is None or item.run_id != run_id:
            return HTMLResponse("条目不存在", status_code=404)
        if action == "skip":
            resolve_item(
                session,
                item_id,
                product_id=item.product_id,
                match_source=item.match_source,
                row_status="stale",
                metric_values={},
                metric_status={key: "stale" for key in ALL_METRICS},
                error_reason="用户跳过待确认条目",
            )
        else:
            raise HTTPException(status_code=400, detail="unsupported resolve action")
        session.add(
            AuditLog(
                actor_id=user.id, action="resolve", object_type="run_item", object_id=str(item_id)
            )
        )
        session.commit()
        return RedirectResponse(f"/updates/{run_id}/preview", status_code=303)

    @app.get("/updates/{run_id}/download")
    def download_update(
        run_id: int,
        user: User = Depends(current_user),
        session: Session = Depends(get_session),
    ):
        run = session.get(UpdateRun, run_id)
        if run is None or not run.output_path or not Path(run.output_path).is_file():
            return HTMLResponse("输出文件不存在", status_code=404)
        return FileResponse(
            run.output_path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=f"净值更新结果-{run_id}.xlsx",
        )

    @app.get("/catalog", response_class=HTMLResponse)
    def catalog_page(
        request: Request,
        user: User = Depends(current_user),
        session: Session = Depends(get_session),
    ):
        products = session.scalars(
            select(Product).where(Product.is_active.is_(True)).order_by(Product.product_code)
        ).all()
        return templates.TemplateResponse(
            request=request,
            name="catalog.html",
            context={"user": user, "products": products, "csrf_token": csrf_token(request)},
        )

    @app.post("/catalog/import")
    async def catalog_import(
        request: Request,
        token: str = Form(...),
        catalog_file: UploadFile | None = File(None),
        user: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ):
        require_csrf(request, token)
        if catalog_file is None:
            return RedirectResponse("/catalog?error=请上传CSV", status_code=303)
        text = (await catalog_file.read()).decode("utf-8-sig")
        records = parse_catalog_csv(text)
        import_catalog(session, records)
        session.add(
            AuditLog(
                actor_id=user.id,
                action="import",
                object_type="catalog",
                object_id="catalog",
            )
        )
        session.commit()
        return RedirectResponse("/catalog", status_code=303)

    @app.get("/admin/users", response_class=HTMLResponse)
    def admin_users_page(
        request: Request,
        user: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ):
        users = session.scalars(select(User).order_by(User.username)).all()
        return templates.TemplateResponse(
            request=request,
            name="admin_users.html",
            context={"user": user, "users": users, "csrf_token": csrf_token(request)},
        )

    @app.post("/admin/users")
    def create_user(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        role: str = Form("user"),
        token: str = Form(...),
        user: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ):
        require_csrf(request, token)
        if role not in {"admin", "user"} or len(password) < 8:
            return RedirectResponse("/admin/users?error=角色或密码不符合要求", status_code=303)
        if session.scalar(select(User).where(User.username == username.strip())) is not None:
            return RedirectResponse("/admin/users?error=账号已存在", status_code=303)
        created = User(username=username.strip(), password_hash=hash_password(password), role=role)
        session.add(created)
        session.flush()
        session.add(
            AuditLog(
                actor_id=user.id, action="create", object_type="user", object_id=str(created.id)
            )
        )
        session.commit()
        return RedirectResponse("/admin/users", status_code=303)

    return app


app = create_app()
