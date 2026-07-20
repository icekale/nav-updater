import re
from datetime import date
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.catalog import private_product_code
from app.config import Settings
from app.db import Base
from app.main import create_app
from app.models import AuditLog, Meeting, Product, RunFile, RunItem, UpdateRun, User


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


def test_login_catalog_upload_and_queue_run(tmp_path: Path) -> None:
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
                    "product_name,product_code,product_type\n"
                    "仁桥金选泽源5B,P001,private\n"
                    "浑瑾岳桐金选1号B,P002,private\n"
                    "开思金选港股通1号B,P003,private\n"
                    "静瑞金选价值灵动1号B,P004,private\n"
                    "勤辰金选创赢成长1号B,P005,private\n"
                    "易方达环保主题灵活配置混合A,P006,private\n",
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
        assert "已进入后台处理队列" in final_page.text
        downloaded = client.get(created.headers["location"].replace("/preview", "/download"))
        assert downloaded.status_code == 404


def test_upload_keeps_same_named_images_separate(tmp_path: Path) -> None:
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

    with TestClient(create_app(settings=settings, session_factory=factory)) as client:
        login_page = client.get("/login")
        token = re.search(r'name="token" value="([^"]+)"', login_page.text).group(1)
        client.post(
            "/login",
            data={"username": "admin", "password": "change-me", "token": token},
            follow_redirects=False,
        )
        new_page = client.get("/updates/new")
        token = re.search(r'name="token" value="([^"]+)"', new_page.text).group(1)
        created = client.post(
            "/updates/new",
            data={"token": token, "cutoff_date": "2026-07-17"},
            files=[
                (
                    "workbook",
                    (
                        "template.xlsx",
                        Path("tests/fixtures/net_value_template.xlsx").read_bytes(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    ),
                ),
                ("images", ("snapshot.png", b"first image", "image/png")),
                ("images", ("snapshot.png", b"second image", "image/png")),
            ],
            follow_redirects=False,
        )
        run_id = int(re.search(r"/updates/(\d+)/preview", created.headers["location"]).group(1))

    session = factory()
    try:
        images = session.query(RunFile).filter_by(run_id=run_id, file_type="image").all()
        assert len(images) == 2
        assert len({image.storage_path for image in images}) == 2
        assert {image.original_name for image in images} == {"snapshot.png"}
        assert {Path(image.storage_path).read_bytes() for image in images} == {
            b"first image",
            b"second image",
        }
    finally:
        session.close()


def test_regenerate_requeues_completed_run_for_worker(tmp_path: Path) -> None:
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

    with TestClient(create_app(settings=settings, session_factory=factory)) as client:
        login_page = client.get("/login")
        token = re.search(r'name="token" value="([^"]+)"', login_page.text).group(1)
        client.post(
            "/login",
            data={"username": "admin", "password": "change-me", "token": token},
            follow_redirects=False,
        )
        session = factory()
        try:
            admin = session.query(User).filter_by(username="admin").one()
            run = UpdateRun(
                operator_id=admin.id,
                cutoff_date=date(2026, 7, 17),
                status="completed",
                output_path=str(tmp_path / "old-result.xlsx"),
            )
            session.add(run)
            session.commit()
            run_id = run.id
        finally:
            session.close()

        preview = client.get(f"/updates/{run_id}/preview")
        token = re.search(r'name="token" value="([^"]+)"', preview.text).group(1)
        requested = client.post(
            f"/updates/{run_id}/process",
            data={"token": token},
            follow_redirects=False,
        )
        assert requested.status_code == 303

    session = factory()
    try:
        run = session.get(UpdateRun, run_id)
        assert run.status == "uploaded"
        assert run.output_path is None
    finally:
        session.close()


def test_manual_review_is_rejected_while_run_is_processing(tmp_path: Path) -> None:
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

    with TestClient(create_app(settings=settings, session_factory=factory)) as client:
        login_page = client.get("/login")
        token = re.search(r'name="token" value="([^"]+)"', login_page.text).group(1)
        client.post(
            "/login",
            data={"username": "admin", "password": "change-me", "token": token},
            follow_redirects=False,
        )
        session = factory()
        try:
            admin = session.query(User).filter_by(username="admin").one()
            product = Product(product_name="产品A", product_code="P001", product_type="private")
            session.add(product)
            session.flush()
            run = UpdateRun(
                operator_id=admin.id,
                cutoff_date=date(2026, 7, 17),
                status="processing",
            )
            session.add(run)
            session.flush()
            item = RunItem(run_id=run.id, excel_row=2, original_values={"product_name": "产品A"})
            session.add(item)
            session.commit()
            run_id = run.id
            item_id = item.id
            product_id = product.id
        finally:
            session.close()

        review = client.get(f"/updates/{run_id}/review")
        token = re.search(r'name="token" value="([^"]+)"', review.text).group(1)
        reviewed = client.post(
            f"/updates/{run_id}/items/{item_id}/review",
            data={
                "token": token,
                "product_choice": f"product:{product_id}",
                "weekly": "12.34",
                "review_note": "人工核对",
            },
            follow_redirects=False,
        )

    assert reviewed.status_code == 409
    session = factory()
    try:
        assert session.get(RunItem, item_id).match_source == "none"
    finally:
        session.close()


def test_review_creates_private_product_and_hides_ready_items(tmp_path: Path) -> None:
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

    with TestClient(create_app(settings=settings, session_factory=factory)) as client:
        login_page = client.get("/login")
        token = re.search(r'name="token" value="([^"]+)"', login_page.text).group(1)
        client.post(
            "/login",
            data={"username": "admin", "password": "change-me", "token": token},
            follow_redirects=False,
        )
        session = factory()
        try:
            admin = session.query(User).filter_by(username="admin").one()
            run = UpdateRun(operator_id=admin.id, cutoff_date=date(2026, 7, 17), status="completed")
            session.add(run)
            session.flush()
            item = RunItem(
                run_id=run.id,
                excel_row=2,
                original_values={"product_name": "测试私募1号"},
                row_status="needs_review",
                metric_values={"weekly": "0.0123"},
                metric_status={"weekly": "extracted", "mtd": "stale"},
            )
            ready_item = RunItem(
                run_id=run.id,
                excel_row=3,
                original_values={"product_name": "完整产品"},
                row_status="ready",
            )
            session.add_all([item, ready_item])
            session.commit()
            run_id = run.id
            item_id = item.id
        finally:
            session.close()

        preview = client.get(f"/updates/{run_id}/preview")
        assert "识别结果" in preview.text
        assert "待人工审核 1 条" in preview.text
        assert "已识别 1 / 12 项" in preview.text
        assert "去审核" in preview.text

        review = client.get(f"/updates/{run_id}/review")
        assert "测试私募1号" in review.text
        assert "完整产品" not in review.text
        assert f'/updates/{run_id}/review?show_all=1' in review.text
        assert 'value="create_private" selected' in review.text
        assert 'class="metric-field missing"' in review.text
        assert "需补录（11 项）" in review.text
        assert "已识别（1 项，可修改）" in review.text
        all_items = client.get(f"/updates/{run_id}/review?show_all=1")
        assert "完整产品" in all_items.text
        token = re.search(r'name="token" value="([^"]+)"', review.text).group(1)
        saved = client.post(
            f"/updates/{run_id}/items/{item_id}/review",
            data={
                "token": token,
                "product_choice": "create_private",
                "weekly": "1.23",
                "mtd": "2.34",
                "review_note": "核对管理人净值表",
            },
            follow_redirects=False,
        )
        assert saved.status_code == 303

    session = factory()
    try:
        product = session.query(Product).filter_by(product_name="测试私募1号").one()
        assert product.product_type == "private"
        assert session.get(RunItem, item_id).product_id == product.id
        assert session.query(AuditLog).filter_by(action="create_private_product").count() == 1
        assert session.query(AuditLog).filter_by(action="manual_review").count() == 1
    finally:
        session.close()


def test_review_keeps_submitted_values_after_private_code_conflict(tmp_path: Path) -> None:
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

    with TestClient(create_app(settings=settings, session_factory=factory)) as client:
        login_page = client.get("/login")
        token = re.search(r'name="token" value="([^"]+)"', login_page.text).group(1)
        client.post(
            "/login",
            data={"username": "admin", "password": "change-me", "token": token},
            follow_redirects=False,
        )
        session = factory()
        try:
            admin = session.query(User).filter_by(username="admin").one()
            product_name = "测试私募冲突"
            session.add(
                Product(
                    product_name="其他产品",
                    product_code=private_product_code(product_name),
                    product_type="private",
                )
            )
            run = UpdateRun(operator_id=admin.id, cutoff_date=date(2026, 7, 17), status="completed")
            session.add(run)
            session.flush()
            item = RunItem(
                run_id=run.id,
                excel_row=2,
                original_values={"product_name": product_name},
                row_status="needs_review",
            )
            session.add(item)
            session.commit()
            run_id = run.id
            item_id = item.id
        finally:
            session.close()

        review = client.get(f"/updates/{run_id}/review")
        token = re.search(r'name="token" value="([^"]+)"', review.text).group(1)
        failed = client.post(
            f"/updates/{run_id}/items/{item_id}/review",
            data={
                "token": token,
                "product_choice": "create_private",
                "weekly": "1.23",
                "review_note": "保留这段说明",
            },
        )

    assert failed.status_code == 422
    assert 'value="1.23"' in failed.text
    assert "保留这段说明" in failed.text
    assert 'value="create_private" selected' in failed.text


def test_review_keeps_draft_after_empty_product_choice(tmp_path: Path) -> None:
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

    with TestClient(create_app(settings=settings, session_factory=factory)) as client:
        login_page = client.get("/login")
        token = re.search(r'name="token" value="([^"]+)"', login_page.text).group(1)
        client.post(
            "/login",
            data={"username": "admin", "password": "change-me", "token": token},
            follow_redirects=False,
        )
        session = factory()
        try:
            admin = session.query(User).filter_by(username="admin").one()
            run = UpdateRun(operator_id=admin.id, cutoff_date=date(2026, 7, 17), status="completed")
            session.add(run)
            session.flush()
            item = RunItem(
                run_id=run.id,
                excel_row=2,
                original_values={"product_name": "测试空选择"},
                row_status="needs_review",
            )
            session.add(item)
            session.commit()
            run_id = run.id
            item_id = item.id
        finally:
            session.close()

        review = client.get(f"/updates/{run_id}/review")
        token = re.search(r'name="token" value="([^"]+)"', review.text).group(1)
        failed = client.post(
            f"/updates/{run_id}/items/{item_id}/review",
            data={
                "token": token,
                "product_choice": "",
                "weekly": "1.23",
                "review_note": "保留这段说明",
            },
        )

    assert failed.status_code == 422
    assert "请选择有效产品" in failed.text
    assert 'value="1.23"' in failed.text
    assert "保留这段说明" in failed.text


def test_review_does_not_create_private_product_for_invalid_metric(tmp_path: Path) -> None:
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

    with TestClient(create_app(settings=settings, session_factory=factory)) as client:
        login_page = client.get("/login")
        token = re.search(r'name="token" value="([^"]+)"', login_page.text).group(1)
        client.post(
            "/login",
            data={"username": "admin", "password": "change-me", "token": token},
            follow_redirects=False,
        )
        session = factory()
        try:
            admin = session.query(User).filter_by(username="admin").one()
            run = UpdateRun(operator_id=admin.id, cutoff_date=date(2026, 7, 17), status="completed")
            session.add(run)
            session.flush()
            item = RunItem(
                run_id=run.id,
                excel_row=2,
                original_values={"product_name": "测试无效指标"},
                row_status="needs_review",
            )
            session.add(item)
            session.commit()
            run_id = run.id
            item_id = item.id
        finally:
            session.close()

        review = client.get(f"/updates/{run_id}/review")
        token = re.search(r'name="token" value="([^"]+)"', review.text).group(1)
        failed = client.post(
            f"/updates/{run_id}/items/{item_id}/review",
            data={
                "token": token,
                "product_choice": "create_private",
                "weekly": "not-a-number",
                "review_note": "保留这段说明",
            },
        )

    assert failed.status_code == 422
    assert 'value="not-a-number"' in failed.text
    assert 'value="create_private" selected' in failed.text
    session = factory()
    try:
        assert session.query(Product).filter_by(product_name="测试无效指标").count() == 0
    finally:
        session.close()


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
                    "product_name,product_code,product_type\n"
                    "仁桥金选泽源5B,P001,private\n"
                    "浑瑾岳桐金选1号B,P002,private\n"
                    "开思金选港股通1号B,P003,private\n"
                    "静瑞金选价值灵动1号B,P004,private\n"
                    "勤辰金选创赢成长1号B,P005,private\n"
                    "易方达环保主题灵活配置混合A,P006,private\n",
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
        product_id = re.search(r'<option value="product:(\d+)"[^>]*>P001', review.text).group(1)
        item_id = re.search(rf'action="/updates/{run_id}/items/(\d+)/review"', review.text).group(1)

        reviewed = client.post(
            f"/updates/{run_id}/items/{item_id}/review",
            data={
                "token": token,
                "product_choice": f"product:{product_id}",
                "weekly": "12.34",
                "review_note": "人工核对管理人净值表",
            },
            follow_redirects=False,
        )
        assert reviewed.status_code == 303
        preview = client.get(f"/updates/{run_id}/preview")
        assert ">人工审核</td>" in preview.text
        token = re.search(r'name="token" value="([^"]+)"', preview.text).group(1)

        processed = client.post(
            f"/updates/{run_id}/process",
            data={"token": token},
            follow_redirects=False,
        )
        assert processed.status_code == 303
        downloaded = client.get(f"/updates/{run_id}/download")
        assert downloaded.status_code == 404

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
