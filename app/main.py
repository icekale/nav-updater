from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="投研净值更新工具", version="0.1.0")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
