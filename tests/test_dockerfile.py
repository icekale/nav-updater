from pathlib import Path


def test_dockerfile_restores_headless_opencv_after_rapidocr_dependency() -> None:
    dockerfile = Path("Dockerfile").read_text()

    assert "pip uninstall -y opencv-python" in dockerfile
    assert (
        'pip install --no-cache-dir --force-reinstall --no-deps '
        '"opencv-python-headless>=4.10,<5"'
    ) in dockerfile
