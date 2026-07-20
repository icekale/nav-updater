from pathlib import Path


def test_ocr_workflow_styles_define_summary_and_review_groups() -> None:
    css = Path("app/static/app.css").read_text()

    assert ".result-summary {" in css
    assert ".result-summary-stats {" in css
    assert ".review-metric-group {" in css
    assert ".review-progress {" in css
    assert ".status {" in css
    assert "white-space: nowrap;" in css
