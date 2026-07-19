import re
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import Base
from app.main import create_app
from app.models import AuditLog, Meeting


def meeting_workbook_bytes() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "近期会议更新"
    sheet.append(["近期资本市场相关会议更新"])
    sheet.append(
        [
            "会议/事件",
            "日期",
            "性质/层级",
            "核心表述",
            "资本市场影响",
            "投研映射",
            "后续跟踪",
            "来源链接",
            "更新时间",
        ]
    )
    sheet.append(
        [
            "2026陆家嘴论坛",
            "2026-06-17至2026-06-18",
            "金融高层论坛",
            "服务高质量发展",
            "投融资综合改革",
            "科技成长",
            "跟踪改革细则",
            "https://example.test/source",
            "2026-07-18",
        ]
    )
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def test_login_page_is_available() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        data_dir=Path("/tmp/nav-updater-test"),
        session_secret="test-secret",
        initial_admin_username="admin",
        initial_admin_password="change-me",
    )
    client = TestClient(create_app(settings=settings, session_factory=factory))
    response = client.get("/login")
    assert response.status_code == 200
    assert "登录" in response.text


def test_login_catalog_upload_process_and_download(tmp_path: Path) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        data_dir=tmp_path,
        session_secret="test-secret",
        initial_admin_username="admin",
        initial_admin_password="change-me",
    )
    app = create_app(settings=settings, session_factory=factory)
    with TestClient(app) as client:
        login_page = client.get("/login")
        token = re.search(r'name="token" value="([^"]+)"', login_page.text).group(1)
        logged_in = client.post(
            "/login",
            data={"username": "admin", "password": "change-me", "token": token},
            follow_redirects=False,
        )
        assert logged_in.status_code == 303

        catalog_page = client.get("/catalog")
        token = re.search(r'name="token" value="([^"]+)"', catalog_page.text).group(1)
        catalog_response = client.post(
            "/catalog/import",
            data={"token": token},
            files={
                "catalog_file": (
                    "catalog.csv",
                    "product_name,product_code,product_type\n仁桥金选泽源5B,P001,private\n",
                    "text/csv",
                )
            },
            follow_redirects=False,
        )
        assert catalog_response.status_code == 303
        assert "仁桥金选泽源5B" in client.get("/catalog").text

        new_page = client.get("/updates/new")
        token = re.search(r'name="token" value="([^"]+)"', new_page.text).group(1)
        workbook = Path("tests/fixtures/net_value_template.xlsx").read_bytes()
        created = client.post(
            "/updates/new",
            data={"token": token, "cutoff_date": "2026-07-17"},
            files={
                "workbook": (
                    "template.xlsx",
                    workbook,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            follow_redirects=False,
        )
        assert created.status_code == 303
        preview = client.get(created.headers["location"])
        assert preview.status_code == 200
        token = re.search(r'name="token" value="([^"]+)"', preview.text).group(1)
        processed = client.post(
            created.headers["location"].replace("/preview", "/process"),
            data={"token": token},
            follow_redirects=False,
        )
        assert processed.status_code == 303
        final_page = client.get(created.headers["location"])
        assert "completed_with_warnings" in final_page.text
        downloaded = client.get(created.headers["location"].replace("/preview", "/download"))
        assert downloaded.status_code == 200
        assert downloaded.headers["content-type"].startswith("application/vnd.openxmlformats")


def test_user_can_manually_review_and_regenerate_a_run(tmp_path: Path) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        data_dir=tmp_path,
        session_secret="test-secret",
        initial_admin_username="admin",
        initial_admin_password="change-me",
    )
    app = create_app(settings=settings, session_factory=factory)
    with TestClient(app) as client:
        login_page = client.get("/login")
        token = re.search(r'name="token" value="([^"]+)"', login_page.text).group(1)
        logged_in = client.post(
            "/login",
            data={"username": "admin", "password": "change-me", "token": token},
            follow_redirects=False,
        )
        assert logged_in.status_code == 303

        catalog_page = client.get("/catalog")
        token = re.search(r'name="token" value="([^"]+)"', catalog_page.text).group(1)
        catalog_response = client.post(
            "/catalog/import",
            data={"token": token},
            files={
                "catalog_file": (
                    "catalog.csv",
                    "product_name,product_code,product_type\n仁桥金选泽源5B,P001,private\n",
                    "text/csv",
                )
            },
            follow_redirects=False,
        )
        assert catalog_response.status_code == 303

        new_page = client.get("/updates/new")
        token = re.search(r'name="token" value="([^"]+)"', new_page.text).group(1)
        created = client.post(
            "/updates/new",
            data={"token": token, "cutoff_date": "2026-07-17"},
            files={
                "workbook": (
                    "template.xlsx",
                    Path("tests/fixtures/net_value_template.xlsx").read_bytes(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            follow_redirects=False,
        )
        assert created.status_code == 303
        run_id = re.search(r"/updates/(\d+)/preview", created.headers["location"]).group(1)

        review = client.get(f"/updates/{run_id}/review")
        assert review.status_code == 200
        assert "人工审核" in review.text
        token = re.search(r'name="token" value="([^"]+)"', review.text).group(1)
        product_id = re.search(r'<option value="(\d+)"[^>]*>P001', review.text).group(1)
        item_id = re.search(rf'action="/updates/{run_id}/items/(\d+)/review"', review.text).group(1)

        reviewed = client.post(
            f"/updates/{run_id}/items/{item_id}/review",
            data={
                "token": token,
                "product_id": product_id,
                "weekly": "12.34",
                "review_note": "人工核对管理人净值表",
            },
            follow_redirects=False,
        )
        assert reviewed.status_code == 303
        preview = client.get(f"/updates/{run_id}/preview")
        assert "manual" in preview.text
        token = re.search(r'name="token" value="([^"]+)"', preview.text).group(1)

        processed = client.post(
            f"/updates/{run_id}/process",
            data={"token": token},
            follow_redirects=False,
        )
        assert processed.status_code == 303
        downloaded = client.get(f"/updates/{run_id}/download")
        assert downloaded.status_code == 200

    session = factory()
    try:
        assert session.query(AuditLog).filter_by(action="manual_review").count() == 1
    finally:
        session.close()


def test_admin_imports_and_user_updates_meeting_record(tmp_path: Path) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        data_dir=tmp_path,
        session_secret="test-secret",
        initial_admin_username="admin",
        initial_admin_password="change-me",
    )
    app = create_app(settings=settings, session_factory=factory)
    with TestClient(app) as admin_client:
        login_page = admin_client.get("/login")
        token = re.search(r'name="token" value="([^"]+)"', login_page.text).group(1)
        admin_client.post(
            "/login",
            data={"username": "admin", "password": "change-me", "token": token},
            follow_redirects=False,
        )

        meetings = admin_client.get("/meetings")
        assert meetings.status_code == 200
        token = re.search(r'name="token" value="([^"]+)"', meetings.text).group(1)
        uploaded = admin_client.post(
            "/meetings/import",
            data={"token": token},
            files={
                "workbook": (
                    "meetings.xlsx",
                    meeting_workbook_bytes(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            follow_redirects=False,
        )
        assert uploaded.status_code == 303
        assert "2026陆家嘴论坛" in admin_client.get("/meetings").text

        users = admin_client.get("/admin/users")
        token = re.search(r'name="token" value="([^"]+)"', users.text).group(1)
        created = admin_client.post(
            "/admin/users",
            data={
                "token": token,
                "username": "researcher",
                "password": "user-pass",
                "role": "user",
            },
            follow_redirects=False,
        )
        assert created.status_code == 303

    with TestClient(app) as user_client:
        login_page = user_client.get("/login")
        token = re.search(r'name="token" value="([^"]+)"', login_page.text).group(1)
        user_client.post(
            "/login",
            data={"username": "researcher", "password": "user-pass", "token": token},
            follow_redirects=False,
        )

        detail = user_client.get("/meetings/1")
        assert detail.status_code == 200
        token = re.search(r'name="token" value="([^"]+)"', detail.text).group(1)
        saved = user_client.post(
            "/meetings/1/record",
            data={
                "token": token,
                "company_tags": "券商, 创投",
                "industry_tags": "金融",
                "attendance_status": "attended",
                "minutes": "已参会，关注投融资改革",
                "todo": "跟踪细则",
                "conclusion": "长期利好",
            },
            follow_redirects=False,
        )
        assert saved.status_code == 303
        assert "已参会" in user_client.get("/meetings/1").text
        assert "2026陆家嘴论坛" in user_client.get("/meetings?company=%E5%88%B8%E5%95%86").text

        invalid = user_client.post(
            "/meetings/1/record",
            data={"token": token, "attendance_status": "invalid"},
            follow_redirects=False,
        )
        assert invalid.status_code == 422

        denied = user_client.post("/meetings/import", data={"token": token}, follow_redirects=False)
        assert denied.status_code == 403

    session = factory()
    try:
        assert session.query(Meeting).count() == 1
        imported = session.query(AuditLog).filter_by(
            action="import", object_type="meeting_workbook"
        )
        updated = session.query(AuditLog).filter_by(action="update", object_type="meeting")
        assert imported.count() == 1
        assert updated.count() == 1
    finally:
        session.close()
