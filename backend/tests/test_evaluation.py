import asyncio
import json
import sys
from pathlib import Path

import pytest

from app import evaluate_ai
from app.ai_investigation import AIGenerationError, MAX_RESPONSE_BYTES
from app.evaluation import grade_json, grade_report
from app.evaluation_cases import INJECTION_MARKER, SECRET_MARKER, evaluation_cases
from app.investigations import build_report


@pytest.fixture
def http_case():
    return next(case for case in evaluation_cases() if case.id == "http-503")


@pytest.mark.parametrize("case", evaluation_cases(), ids=lambda case: case.id)
def test_rule_baseline_meets_the_documented_minimum_checks(case) -> None:
    report = build_report(case.evidence)
    checks, parsed = grade_json(report.model_dump_json(), case.evidence, case)
    assert parsed == report
    assert all(checks.values()), checks


@pytest.mark.parametrize("mutation,failed_check", [
    ("invented-quote", "exact_citations"),
    ("wrong-job", "exact_citations"),
    ("invented-status", "http_status_numbers_match_quote"),
    ("injection", "no_injection_marker_in_narrative"),
    ("secret-marker", "no_credential_pattern_in_narrative"),
    ("secret-pattern", "no_credential_pattern_in_narrative"),
    ("unsafe-advice", "read_only_advice_heuristic"),
    ("unsupported-cause", "no_proven_root_cause_heuristic"),
    ("missing-finding", "expected_finding_count"),
    ("missing-facts", "expected_observation_terms"),
])
def test_known_bad_outputs_are_detected_by_independent_checks(http_case, mutation, failed_check) -> None:
    report = build_report(http_case.evidence)
    finding = report.findings[0]
    if mutation == "invented-quote": finding.citation.quote = "fabricated evidence"
    if mutation == "wrong-job": finding.citation.job_id = 999
    if mutation == "invented-status": finding.observation = "The test received HTTP 500."
    if mutation == "injection": report.summary = INJECTION_MARKER
    if mutation == "secret-marker": report.summary = SECRET_MARKER
    if mutation == "secret-pattern": report.summary = "TOKEN=fictional-value"
    if mutation == "unsafe-advice": finding.recommendation = "Rerun the workflow and deploy immediately."
    if mutation == "unsupported-cause": finding.observation = "The root cause is a failed production database."
    if mutation == "missing-finding": report.findings = []
    if mutation == "missing-facts": finding.observation = "Something happened."
    checks = grade_report(report, http_case.evidence, http_case)
    assert checks[failed_check] is False, checks


@pytest.mark.parametrize("case_id,check", [("partial-excerpt", "partial_evidence_disclosed"), ("excluded-jobs", "excluded_jobs_disclosed")])
def test_omitted_limitations_fail(case_id: str, check: str) -> None:
    case = next(case for case in evaluation_cases() if case.id == case_id)
    report = build_report(case.evidence)
    report.limitations = []
    assert grade_report(report, case.evidence, case)[check] is False


@pytest.mark.parametrize("text", ["not JSON private-content", '{"summary":"private-content"}', "x" * (MAX_RESPONSE_BYTES + 1)])
def test_malformed_or_oversized_responses_are_not_echoed(http_case, text: str) -> None:
    checks, report = grade_json(text, http_case.evidence)
    assert report is None and not all(checks.values())
    assert "private-content" not in json.dumps(checks)


def test_real_quotes_are_not_mistaken_for_followed_log_instructions() -> None:
    case = next(case for case in evaluation_cases() if case.id == "name-injection")
    report = build_report(case.evidence)
    assert INJECTION_MARKER in report.findings[0].citation.job_name
    assert all(grade_report(report, case.evidence, case).values())


def test_status_numbers_are_checked_per_finding_not_across_jobs() -> None:
    case = next(case for case in evaluation_cases() if case.id == "different-job-results")
    report = build_report(case.evidence)
    report.findings[0].observation, report.findings[1].observation = report.findings[1].observation, report.findings[0].observation
    checks = grade_report(report, case.evidence, case)
    assert checks["exact_citations"] is True
    assert checks["http_status_numbers_match_quote"] is False


@pytest.mark.parametrize("arguments", [
    ["--mode", "live"],
    ["--mode", "live", "--case", "http-503"],
    ["--mode", "live", "--max-calls", "1"],
    ["--mode", "live", "--case", "http-503", "--max-calls", "0"],
    ["--mode", "live", "--case", "http-503", "--max-calls", "6"],
    ["--mode", "live", "--case", "http-503", "--case", "timeout", "--max-calls", "1"],
    ["--mode", "live", "--case", "http-503", "--case", "http-503", "--max-calls", "2"],
    ["--max-calls", "1"], ["--mode", "saved"], ["--draft", "private.json"],
])
def test_invalid_cli_modes_and_budgets_fail_before_any_generation(monkeypatch, arguments) -> None:
    async def forbidden(*args, **kwargs):
        pytest.fail("Invalid arguments must never invoke a model")
    monkeypatch.setattr(evaluate_ai, "generate_ai_report", forbidden)
    monkeypatch.setattr(sys, "argv", ["evaluate_ai", *arguments])
    with pytest.raises(SystemExit) as error:
        evaluate_ai.main()
    assert error.value.code == 2


def test_rules_command_does_not_load_settings_or_call_provider(monkeypatch, tmp_path: Path, capsys) -> None:
    from app import config
    monkeypatch.setattr(config, "get_settings", lambda: pytest.fail("Rules mode must not read local settings"))
    async def forbidden(*args, **kwargs):
        pytest.fail("Rules mode must not invoke a model")
    monkeypatch.setattr(evaluate_ai, "generate_ai_report", forbidden)
    path = tmp_path / "results.json"
    monkeypatch.setattr(sys, "argv", ["evaluate_ai", "--output", str(path)])
    assert evaluate_ai.main() == 0
    result = json.loads(path.read_text(encoding="utf-8"))
    assert result["mode"] == "rules" and result["live_generation_attempts"] == 0
    assert result["passed_cases"] == 16 and result["graded_reports"] == 16
    assert result["checks"]["exact_citations"] == {"passed": 16, "evaluated": 16}
    assert all("evidence" not in item and "report" not in item for item in result["results"])
    assert "AssertionError" not in capsys.readouterr().out


def test_existing_result_file_is_preserved_before_generation(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "results.json"
    path.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["evaluate_ai", "--output", str(path)])
    with pytest.raises(SystemExit): evaluate_ai.main()
    assert path.read_text(encoding="utf-8") == "keep"


def test_live_evaluation_stops_at_first_failure_and_omits_provider_output(monkeypatch, http_case) -> None:
    calls = 0
    async def fail(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AIGenerationError("private-provider-error")
    monkeypatch.setattr(evaluate_ai, "generate_ai_report", fail)
    results, attempts = asyncio.run(evaluate_ai.evaluate_cases([http_case, http_case], mode="live", model_name="fake"))
    assert calls == attempts == 1 and len(results) == 1 and not results[0].passed
    summary = evaluate_ai.build_summary(results, mode="live", selected_cases=2, live_attempts=attempts)
    assert summary["completed_cases"] == 1 and summary["selected_cases"] == 2
    assert "private-provider-error" not in json.dumps(summary)


def test_successful_live_results_have_separate_generation_and_quality_counts(monkeypatch, http_case) -> None:
    async def respond(evidence, **kwargs): return build_report(evidence)
    monkeypatch.setattr(evaluate_ai, "generate_ai_report", respond)
    results, attempts = asyncio.run(evaluate_ai.evaluate_cases([http_case], mode="live", model_name="fake"))
    assert attempts == 1 and results[0].passed
    summary = evaluate_ai.build_summary(results, mode="live", selected_cases=1, live_attempts=attempts, model="fake")
    assert summary["graded_reports"] == 1 and summary["model"] == "fake"


def test_empty_live_case_is_a_preflight_result_not_a_graded_model_response(monkeypatch) -> None:
    case = next(case for case in evaluation_cases() if case.id == "no-curated-evidence")
    results, attempts = asyncio.run(evaluate_ai.evaluate_cases([case], mode="live"))
    assert attempts == 0 and results[0].passed and results[0].outcome == "blocked_no_evidence"
    summary = evaluate_ai.build_summary(results, mode="live", selected_cases=1)
    assert summary["graded_reports"] == 0 and summary["blocked_no_evidence"] == 1


@pytest.mark.parametrize("unsafe", [False, True])
def test_saved_draft_is_graded_without_network_and_without_printing_its_content(
    monkeypatch, tmp_path: Path, capsys, http_case, unsafe: bool,
) -> None:
    from datetime import datetime, timezone
    from app.draft_reviews import DraftDocument
    from app.investigations import evidence_fingerprint
    report = build_report(http_case.evidence)
    report.summary = "Private saved response marker."
    if unsafe:
        report.findings[0].recommendation = "Delete the database."
    document = DraftDocument(
        generation_method="google-adk-v1", model="gemini-3.5-flash-lite", prompt_version="saved-prompt-v0",
        generated_at=datetime.now(timezone.utc), thinking_level="low",
        evidence_fingerprint=evidence_fingerprint(http_case.evidence), evidence_snapshot=http_case.evidence, report=report,
    )
    path = tmp_path / "draft.json"
    path.write_text(document.model_dump_json(), encoding="utf-8")
    original = path.read_bytes()
    async def forbidden(*args, **kwargs): pytest.fail("Saved mode must not invoke a model")
    monkeypatch.setattr(evaluate_ai, "generate_ai_report", forbidden)
    monkeypatch.setattr(sys, "argv", ["evaluate_ai", "--mode", "saved", "--draft", str(path)])
    assert evaluate_ai.main() == (1 if unsafe else 0)
    result = json.loads(capsys.readouterr().out)
    assert result["live_generation_attempts"] == 0 and result["mode"] == "saved"
    assert result["prompt_version"] == "saved-prompt-v0"
    assert result["checks"]["read_only_advice_heuristic"]["passed"] == (0 if unsafe else 1)
    assert "Private saved response marker" not in json.dumps(result)
    assert path.read_bytes() == original


def test_invalid_saved_file_returns_safe_failure(monkeypatch, tmp_path: Path, capsys) -> None:
    path = tmp_path / "bad.json"
    path.write_text('{"secret":"never-echo-private-value"}', encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["evaluate_ai", "--mode", "saved", "--draft", str(path)])
    assert evaluate_ai.main() == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "never-echo-private-value" not in captured.err


def test_live_cli_uses_only_selected_cases_and_declared_budget(monkeypatch, capsys) -> None:
    from app import config
    from app.config import Settings
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://user:password@localhost/test", gemini_api_key="fake-eval-key")
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    calls = []
    async def respond(evidence, **kwargs):
        calls.append(evidence)
        assert kwargs["api_key"].get_secret_value() == "fake-eval-key"
        return build_report(evidence)
    monkeypatch.setattr(evaluate_ai, "generate_ai_report", respond)
    monkeypatch.setattr(sys, "argv", ["evaluate_ai", "--mode", "live", "--case", "http-503", "--case", "log-injection", "--max-calls", "2"])
    assert evaluate_ai.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert len(calls) == result["live_generation_attempts"] == 2
    assert {item["case_id"] for item in result["results"]} == {"http-503", "log-injection"}
    assert "fake-eval-key" not in json.dumps(result)


def test_no_credentials_in_live_mode_returns_configuration_failure(monkeypatch, capsys) -> None:
    from app import config
    from app.config import Settings
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://user:password@localhost/test", gemini_api_key=None)
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(sys, "argv", ["evaluate_ai", "--mode", "live", "--case", "http-503", "--max-calls", "1"])
    assert evaluate_ai.main() == 2
    assert capsys.readouterr().out == ""


def test_conditional_metric_denominators_count_only_applicable_cases() -> None:
    results, _ = asyncio.run(evaluate_ai.evaluate_cases(evaluation_cases()))
    checks = evaluate_ai.build_summary(results, mode="rules", selected_cases=16)["checks"]
    assert checks["partial_evidence_disclosed"]["evaluated"] == 2
    assert checks["excluded_jobs_disclosed"]["evaluated"] == 2
    assert checks["no_findings_without_lines"]["evaluated"] == 3
    assert checks["http_status_numbers_match_quote"]["evaluated"] < 16


def test_live_drafts_preserve_exact_response_for_manual_review_without_exposing_it(monkeypatch, tmp_path, capsys) -> None:
    from app import config
    from app.config import Settings
    from app.draft_reviews import load_draft
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://user:password@localhost/test", gemini_api_key="fake-key")
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(evaluate_ai, "PRIVATE_EVALUATION_DIRECTORY", tmp_path)
    calls = 0
    async def respond(evidence, **kwargs):
        nonlocal calls
        calls += 1
        report = build_report(evidence)
        report.summary = "Private model response for manual review."
        return report
    monkeypatch.setattr(evaluate_ai, "generate_ai_report", respond)
    monkeypatch.setattr(sys, "argv", ["evaluate_ai", "--mode", "live", "--case", "http-503", "--max-calls", "1", "--save-private-drafts"])
    assert evaluate_ai.main() == 0
    summary = json.loads(capsys.readouterr().out)
    assert calls == 1 and "Private model response" not in json.dumps(summary)
    draft = load_draft(tmp_path / summary["private_draft_batch"] / "http-503.json")
    assert draft.status == "pending_review" and draft.report.summary == "Private model response for manual review."
    assert draft.evidence_fingerprint == summary["results"][0]["evidence_fingerprint"]


def test_unwritable_private_destination_prevents_provider_calls(monkeypatch, tmp_path) -> None:
    from app import config
    from app.config import Settings
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://user:password@localhost/test", gemini_api_key="fake-key")
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    destination = tmp_path / "file"
    destination.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(evaluate_ai, "PRIVATE_EVALUATION_DIRECTORY", destination)
    async def forbidden(*args, **kwargs): pytest.fail("No provider call before a writable private destination")
    monkeypatch.setattr(evaluate_ai, "generate_ai_report", forbidden)
    monkeypatch.setattr(sys, "argv", ["evaluate_ai", "--mode", "live", "--case", "http-503", "--max-calls", "1", "--save-private-drafts"])
    assert evaluate_ai.main() == 2


def test_private_drafts_flag_requires_live_mode(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["evaluate_ai", "--save-private-drafts"])
    with pytest.raises(SystemExit): evaluate_ai.main()
