"""Evaluate investigations locally. Only explicit --mode live can call Gemini."""
import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from pydantic import SecretStr, ValidationError

from app.ai_investigation import AIGenerationError, INSTRUCTION, PROMPT_VERSION, generate_ai_report
from app.draft_reviews import DraftReviewError, load_draft
from app.evaluation import CaseResult, RUBRIC_VERSION, grade_json, grade_report
from app.evaluation_cases import CORPUS_VERSION, EvaluationCase, evaluation_cases
from app.investigations import EvidenceBundle, build_report, document_fingerprint, evidence_fingerprint
from app.schemas import InvestigationReport

MAX_LIVE_CALLS = 5


def _result(case_id: str, category: str, outcome: str, evidence: EvidenceBundle, checks: dict[str, bool], report: InvestigationReport | None = None) -> CaseResult:
    return CaseResult(case_id, category, outcome, checks, evidence_fingerprint(evidence),
        document_fingerprint(report.model_dump(mode="json")) if report is not None else None)


async def evaluate_cases(cases: Sequence[EvaluationCase], *, mode: str = "rules", api_key: SecretStr | None = None, model_name: str | None = None) -> tuple[list[CaseResult], int]:
    results = []
    attempts = 0
    for case in cases:
        if mode == "live" and not any(job.lines for job in case.evidence.jobs):
            # Exercise the production preflight without creating a provider client.
            try:
                await generate_ai_report(case.evidence, api_key=api_key, model_name=model_name)
            except AIGenerationError:
                results.append(_result(case.id, case.category, "blocked_no_evidence", case.evidence, {"empty_evidence_preflight": True}))
                continue
            results.append(_result(case.id, case.category, "unexpected_generation", case.evidence, {"empty_evidence_preflight": False}))
            continue
        try:
            if mode == "live":
                attempts += 1
                report = await generate_ai_report(case.evidence, api_key=api_key, model_name=model_name)
            else:
                report = build_report(case.evidence)
            checks, parsed = grade_json(report.model_dump_json(), case.evidence, case)
            results.append(_result(case.id, case.category, "graded", case.evidence, checks, parsed))
        except AIGenerationError:
            # Generation already enforces format/citation validity. A rejection
            # is an unsuccessful generation, never a quality pass or a retry.
            results.append(_result(case.id, case.category, "generation_failed", case.evidence, {"generation_succeeded": False}))
            break  # Stop spending the explicit budget after the first failure.
    return results, attempts


def build_summary(results: list[CaseResult], *, mode: str, selected_cases: int, live_attempts: int = 0, model: str | None = None, prompt_version: str = PROMPT_VERSION) -> dict:
    names = sorted({name for result in results for name in result.checks})
    return {
        "format_version": 1, "corpus_version": CORPUS_VERSION if mode != "saved" else None, "rubric_version": RUBRIC_VERSION,
        "instruction_fingerprint": document_fingerprint({"instruction": INSTRUCTION}) if mode == "live" else None,
        "corpus_fingerprint": document_fingerprint({"cases": [{
            "id": case.id, "evidence": case.evidence.model_dump(mode="json"),
            "min_findings": case.min_findings, "max_findings": case.max_findings,
            "observation_terms": case.observation_terms,
        } for case in evaluation_cases()]}) if mode != "saved" else None,
        "evaluated_at": datetime.now(timezone.utc).isoformat(), "mode": mode,
        "model": model, "prompt_version": prompt_version if mode != "rules" else None, "live_generation_attempts": live_attempts,
        "selected_cases": selected_cases, "completed_cases": len(results),
        "passed_cases": sum(result.passed for result in results),
        "graded_reports": sum(result.outcome == "graded" for result in results),
        "blocked_no_evidence": sum(result.outcome == "blocked_no_evidence" for result in results),
        "checks": {name: {
            "passed": sum(result.checks.get(name) is True for result in results),
            "evaluated": sum(name in result.checks for result in results),
        } for name in names},
        "results": [{**asdict(result), "passed": result.passed} for result in results],
        "limits": [
            "Exact citations verify source identity and text, not the truth of an observation.",
            "Keyword checks are incomplete and can flag safe wording; manual review is required.",
            "Quoted source text and job names are excluded from narrative policy checks.",
            "Rules and saved-draft results do not measure new live model behavior.",
        ],
    }


def main() -> int:
    cases = evaluation_cases()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("rules", "saved", "live"), default="rules")
    parser.add_argument("--case", dest="case_ids", action="append", choices=[case.id for case in cases], help="Repeat to select cases; required in live mode")
    parser.add_argument("--max-calls", type=int, help=f"Required explicit live budget, 1-{MAX_LIVE_CALLS}; one request per selected case, no retries")
    parser.add_argument("--draft", type=Path, help="Private draft JSON for saved mode; checked locally without model calls")
    parser.add_argument("--output", type=Path, help="Write metadata/checks only to a NEW JSON file; existing files are not overwritten")
    args = parser.parse_args()
    selected = [case for case in cases if args.case_ids is None or case.id in args.case_ids]
    if args.case_ids and len(args.case_ids) != len(set(args.case_ids)):
        parser.error("Select each case only once.")
    if args.mode == "live":
        if not args.case_ids or args.max_calls is None or not 1 <= args.max_calls <= MAX_LIVE_CALLS:
            parser.error(f"Live mode requires explicit --case selections and --max-calls between 1 and {MAX_LIVE_CALLS}.")
        if len(selected) > args.max_calls:
            parser.error("Selected cases exceed the explicit call budget.")
    elif args.max_calls is not None:
        parser.error("--max-calls is only valid with --mode live.")
    if (args.mode == "saved") != (args.draft is not None) or (args.mode == "saved" and args.case_ids):
        parser.error("Saved mode requires --draft and does not accept --case; it uses general checks without scenario labels.")
    if args.output is not None and (args.output.exists() or args.draft is not None and args.output.resolve() == args.draft.resolve()):
        parser.error("Choose a new output file; draft and result files cannot be overwritten.")
    try:
        model = None
        prompt_version = PROMPT_VERSION
        if args.mode == "saved":
            document = load_draft(args.draft)
            results = [_result("saved-draft", "saved", "graded", document.evidence_snapshot,
                grade_report(document.report, document.evidence_snapshot), document.report)]
            attempts, selected_count = 0, 1
            model, prompt_version = document.model, document.prompt_version
        else:
            key = None
            if args.mode == "live":
                from app.config import get_settings
                settings = get_settings()
                key, model = settings.gemini_api_key, settings.ai_model
                if key is None or not key.get_secret_value().strip():
                    print("Configure GEMINI_API_KEY locally before using live mode.", file=sys.stderr)
                    return 2
            results, attempts = asyncio.run(evaluate_cases(selected, mode=args.mode, api_key=key, model_name=model))
            selected_count = len(selected)
        summary = build_summary(results, mode=args.mode, selected_cases=selected_count, live_attempts=attempts, model=model, prompt_version=prompt_version)
        serialized = json.dumps(summary, indent=2) + "\n"
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8", newline="\n") as output:
                output.write(serialized)
        print(serialized, end="")
        return 0 if len(results) == selected_count and all(result.passed for result in results) else 1
    except (OSError, ValueError, ValidationError, DraftReviewError):
        print("Evaluation input, configuration or output file is invalid. No report content was printed.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
