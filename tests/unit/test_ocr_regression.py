from app.models import OcrRegressionResult, OcrRegressionRun, OcrRegressionSample, RunItem


def test_regression_models_keep_sample_when_source_run_is_deleted() -> None:
    assert OcrRegressionSample.__tablename__ == "ocr_regression_samples"
    assert OcrRegressionRun.__tablename__ == "ocr_regression_runs"
    assert OcrRegressionResult.__tablename__ == "ocr_regression_results"
    assert "ocr_evidence" in RunItem.__table__.c
    assert OcrRegressionSample.source_run_id.property.columns[0].nullable is True
    assert OcrRegressionSample.created_at.default is not None
