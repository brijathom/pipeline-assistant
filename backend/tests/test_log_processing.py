import pytest

from app.log_processing import (
    MAX_INPUT_BYTES, MAX_OUTPUT_BYTES, MAX_OUTPUT_LINES, REDACTED, TRUNCATED,
    LogProcessingError, process_log,
)


@pytest.mark.parametrize("raw", [
    b"ghp_FAKE_token_for_tests", b"github_pat_FAKE_token_for_tests",
    b"gho_FAKE_token", b"ghu_FAKE_token", b"ghr_FAKE_token",
    b"ghs_123_FAKE.header.signature", b"eyJmYWtl.eyJ0ZXN0.fake_signature",
])
def test_recognizable_tokens_are_redacted(raw: bytes) -> None:
    result = process_log(b"Build output: " + raw)
    assert result.text == "Build output: " + REDACTED
    assert result.redactions_applied


@pytest.mark.parametrize("line, expected", [
    ("PASSWORD=fake-password", "PASSWORD=[REDACTED]"),
    ('{"password": "fake-password", "result": "failed"}', '{"password": [REDACTED]'),
    ("timestamp=12:00 level=ERROR CI_ACCESS_TOKEN=fake-token", "timestamp=12:00 level=ERROR CI_ACCESS_TOKEN=[REDACTED]"),
    ("AWS_SECRET_ACCESS_KEY=fake-key", "AWS_SECRET_ACCESS_KEY=[REDACTED]"),
    ("Authorization: Bearer fake-token", "Authorization: [REDACTED]"),
    ("Proxy-Authorization: Basic fake-base64", "Proxy-Authorization: [REDACTED]"),
    ("curl --password fake-password --verbose", "curl --password [REDACTED]"),
    ("tool --token=fake-token", "tool --token=[REDACTED]"),
    ("Using Bearer fake-token", "Using Bearer [REDACTED]"),
    ("https://test-user:fake-password@example.invalid/path", "https://[REDACTED]@example.invalid/path"),
    ("postgresql+psycopg://test-user:fake-password@localhost/test", "postgresql+psycopg://[REDACTED]@localhost/test"),
])
def test_common_credential_contexts_are_masked(line: str, expected: str) -> None:
    result = process_log(line.encode())
    assert result.text == expected
    assert result.redactions_applied


@pytest.mark.parametrize("closed", [True, False])
def test_private_keys_are_redacted_before_selecting_middle_lines(closed: bool) -> None:
    text = "Build started\n-----BEGIN OPENSSH PRIVATE KEY-----\nfake-key-body\nmore-fake-key-body\n"
    if closed:
        text += "-----END OPENSSH PRIVATE KEY-----\nBuild failed\n"
    result = process_log(text.encode(), start_line=3, max_lines=2)
    assert [line.source_line for line in result.lines] == [3, 4]
    assert result.text == f"{REDACTED}\n{REDACTED}"
    assert result.total_source_lines == (6 if closed else 4)
    assert result.truncated and result.redactions_applied


def test_terminal_controls_are_removed_before_token_detection() -> None:
    text = "\x1b[31mghp_FAKE_\x1b[0mto\u200bken\x00\x08\x1b[2K\n"
    result = process_log(text.encode())
    assert result.text == REDACTED
    assert result.total_source_lines == 1
    assert result.redactions_applied


def test_multiline_terminal_strings_preserve_source_line_numbers() -> None:
    text = "First\n\x1b]title: hidden\nstill hidden\x07Visible\n\x9b31mLast\x9b0m\n"
    result = process_log(text.encode())
    assert [line.source_line for line in result.lines] == [1, 2, 3, 4]
    assert [line.text for line in result.lines] == ["First", "", "Visible", "Last"]


def test_newlines_and_empty_lines_keep_source_references() -> None:
    result = process_log(b"first\r\n\r\nthird\rfourth\n", start_line=2, max_lines=2)
    assert [(line.source_line, line.text) for line in result.lines] == [(2, ""), (3, "third")]
    assert result.total_source_lines == 4
    assert result.truncated


def test_redaction_happens_before_the_output_byte_limit() -> None:
    raw = b"token=" + b"fake-secret-value-" * 100
    result = process_log(raw, max_bytes=16)
    assert result.text == "token=[REDACTED]"
    assert result.redactions_applied
    assert not result.truncated


def test_utf8_clipping_and_line_separators_respect_the_byte_budget() -> None:
    result = process_log(("first\n" + "🙂" * 30).encode(), max_bytes=24)
    assert result.lines[0].text == "first"
    assert result.lines[1].source_line == 2
    assert result.lines[1].text.endswith(TRUNCATED)
    assert len(result.text.encode("utf-8")) <= 24
    assert "�" not in result.text
    assert result.truncated


def test_line_count_cap_reports_omitted_evidence() -> None:
    result = process_log(b"line\n" * (MAX_OUTPUT_LINES + 1))
    assert len(result.lines) == MAX_OUTPUT_LINES
    assert result.total_source_lines == MAX_OUTPUT_LINES + 1
    assert result.truncated


def test_a_line_is_omitted_if_its_marker_cannot_fit() -> None:
    result = process_log(b"12345678901\nsecond-line", max_bytes=11)
    assert result.text == "12345678901"
    assert result.truncated


@pytest.mark.parametrize("raw, expected_lines", [(b"", []), (b"\n", [""]), (b"\n\n", ["", ""]), (b"\x1b[31m", [""])])
def test_empty_and_control_only_inputs(raw: bytes, expected_lines: list[str]) -> None:
    result = process_log(raw)
    assert [line.text for line in result.lines] == expected_lines
    assert result.total_source_lines == len(expected_lines)
    assert not result.truncated


def test_ordinary_diagnostics_and_unicode_are_preserved() -> None:
    text = "2026-11-08 tests failed: expected 200, got 500\nRésumé 🙂\nTOKEN_COUNT=12\n"
    result = process_log(text.encode())
    assert result.text == text.removesuffix("\n")
    assert not result.redactions_applied
    assert not result.truncated


@pytest.mark.parametrize("options", [
    {"start_line": 0}, {"start_line": 3}, {"max_lines": 0}, {"max_lines": MAX_OUTPUT_LINES + 1},
    {"max_bytes": 10}, {"max_bytes": MAX_OUTPUT_BYTES + 1}, {"max_lines": True},
])
def test_invalid_limits_are_rejected(options: dict) -> None:
    with pytest.raises(LogProcessingError):
        process_log(b"one\ntwo", **options)


@pytest.mark.parametrize("raw", [b"fake-sensitive-data" + b"x" * MAX_INPUT_BYTES, b"fake-sensitive-data\xff"])
def test_invalid_input_errors_do_not_echo_log_contents(raw: bytes) -> None:
    with pytest.raises(LogProcessingError) as error:
        process_log(raw)
    assert "fake-sensitive-data" not in str(error.value)


def test_unlabelled_custom_values_are_outside_pattern_detection() -> None:
    # This limitation is intentional and documented; redaction is not a publication gate.
    result = process_log(b"unlabelled-custom-value")
    assert result.text == "unlabelled-custom-value"
    assert not result.redactions_applied
