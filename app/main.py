from __future__ import annotations

import shutil
import uuid
from datetime import date
from pathlib import Path

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
from .jobs.service import create_run, resolve_item
from .models import AuditLog, Product, RunItem, UpdateRun, User


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
