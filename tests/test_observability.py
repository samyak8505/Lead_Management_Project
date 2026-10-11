import pytest
from leadflow.observability.tracer import Tracer, get_tracer
from leadflow.observability.report import generate_metrics_report, format_ascii_report, generate_markdown_report


@pytest.fixture
def tracer_instance(tmp_path):
    db_path = tmp_path / "test_telemetry.db"
    tracer = Tracer(db_path)
    return tracer, str(db_path)


def test_tracer_records_trace_and_spans(tracer_instance):
    tracer, db_path = tracer_instance
    trace = tracer.start_trace(run_id="run-test-001", name="test-pipeline", tags={"tier": "hot"})

    with tracer.span("extract", metadata={"channel": "email"}) as sp:
        sp.input_tokens = 500
        sp.output_tokens = 100

    with tracer.span("score") as sp:
        sp.metadata["score"] = 90

    ended = tracer.end_trace(status="completed")
    assert ended.status == "completed"
    assert ended.duration_ms >= 0.0
    assert ended.input_tokens == 500
    assert ended.output_tokens == 100
    assert ended.cost > 0.0

    # Retrieve from DB
    loaded = tracer.get_trace(trace.trace_id)
    assert loaded is not None
    assert loaded.run_id == "run-test-001"
    assert len(loaded.spans) == 2
    assert loaded.spans[0].name == "extract"
    assert loaded.spans[0].input_tokens == 500
    assert loaded.spans[1].name == "score"


def test_span_captures_exceptions(tracer_instance):
    tracer, _ = tracer_instance
    tracer.start_trace(run_id="run-test-error")

    with pytest.raises(ValueError, match="simulated failure"):
        with tracer.span("failing_step"):
            raise ValueError("simulated failure")

    ended = tracer.end_trace(status="failed", error="simulated failure")
    assert ended.status == "failed"
    assert ended.error == "simulated failure"
    assert ended.spans[0].status == "error"
    assert "simulated failure" in ended.spans[0].error


def test_metrics_report_generation(tracer_instance, tmp_path):
    tracer, db_path = tracer_instance

    # Record 2 sample traces
    tracer.start_trace(run_id="run-1", tags={"channel": "email"})
    with tracer.span("extract") as sp:
        sp.input_tokens = 400
        sp.output_tokens = 80
    with tracer.span("write_back"):
        pass
    tracer.end_trace(status="completed")

    tracer.start_trace(run_id="run-2", tags={"channel": "web_form"})
    with tracer.span("extract") as sp:
        sp.input_tokens = 600
        sp.output_tokens = 120
    tracer.end_trace(status="discarded")

    # Generate report
    report = generate_metrics_report(db_path)
    assert report["total_runs"] == 2
    assert report["status_breakdown"]["completed"] == 1
    assert report["status_breakdown"]["discarded"] == 1
    assert report["avg_cost_dollars"] > 0.0
    assert "extract" in report["per_step_metrics"]
    assert report["per_step_metrics"]["extract"]["invocations"] == 2

    # Verify ASCII formatter
    ascii_out = format_ascii_report(report)
    assert "LEADFLOW OBSERVABILITY & METRICS REPORT" in ascii_out
    assert "Total Pipeline Runs    : 2" in ascii_out
    assert "extract" in ascii_out

    # Verify Markdown generator
    md_file = tmp_path / "test_report.md"
    generate_markdown_report(report, str(md_file))
    assert md_file.exists()
    content = md_file.read_text(encoding="utf-8")
    assert "Average Cost per Lead" in content
    assert "Per-Step Latency" in content
