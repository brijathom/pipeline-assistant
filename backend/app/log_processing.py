"""Bounded log excerpts, with best-effort redaction; no I/O or raw-log logging.

Pattern matching is not a guarantee that all secrets are removed. Only curated
evidence should be exposed in the public demo or sent to an AI provider.
"""
import re
import unicodedata
from dataclasses import dataclass

MAX_INPUT_BYTES = 1_048_576
MAX_OUTPUT_BYTES = 16_384
MAX_OUTPUT_LINES = 100
REDACTED = "[REDACTED]"
TRUNCATED = "[truncated]"

_CONTROL_STRING = re.compile(r"(?:\x1b[\]PX^_]|[\x90\x98\x9d\x9e\x9f]).*?(?:\x07|\x1b\\|\x9c|\Z)", re.DOTALL)
_CSI = re.compile(r"(?:\x1b\[|\x9b)[0-?]*[ -/]*(?:[@-~]|\Z)")
_ESCAPE = re.compile(r"\x1b[ -/]*[@-~]")
_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?P<label>(?:[A-Z]+ )?PRIVATE KEY)-----.*?(?:-----END (?P=label)-----|\Z)",
    re.DOTALL,
)
_GITHUB_TOKEN = re.compile(r"\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_.-]+")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_AUTH_VALUE = re.compile(r"\b(?P<prefix>(?:Bearer|Basic)[ \t]+)[^\s\"'<>]+", re.IGNORECASE)
_URL_USERINFO = re.compile(r"\b(?P<scheme>[a-z][a-z0-9+.-]{0,31}://)[^\s/?#@]+@", re.IGNORECASE)
# Match only the key and separator, so an innocent earlier field cannot hide a
# sensitive assignment later on the same line. Key lengths are deliberately bounded.
_KEY_PREFIX = re.compile(
    r"(?<![\w-])(?P<quote>[\"']?)(?P<key>[A-Za-z_][A-Za-z0-9_-]{0,127})(?P=quote)[ \t]*[:=][ \t]*",
)
_SECRET_FLAG = re.compile(
    r"(?<!\w)--(?:password|passwd|token|api-key|client-secret)(?:[ \t]*=[ \t]*|[ \t]+)",
    re.IGNORECASE,
)
_SENSITIVE_SUFFIXES = (
    "password", "passwd", "pwd", "secret", "token", "api_key", "private_key",
    "secret_access_key", "database_url", "connection_string", "authorization",
)


class LogProcessingError(ValueError):
    """An input or limit error that never includes raw log contents."""


@dataclass(frozen=True, slots=True)
class LogLine:
    source_line: int
    text: str


@dataclass(frozen=True, slots=True)
class LogExcerpt:
    lines: tuple[LogLine, ...]
    total_source_lines: int
    truncated: bool
    redactions_applied: bool

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)


def _strip_controls(text: str) -> str:
    # Preserve physical newline counts even when a control string spans lines.
    text = _CONTROL_STRING.sub(lambda match: "\n" * match.group().count("\n"), text)
    text = _CSI.sub("", text)
    text = _ESCAPE.sub("", text)
    return "".join(
        char for char in text
        if char in "\n\t" or unicodedata.category(char) not in {"Cc", "Cf"}
    )


def _redact(text: str) -> tuple[str, bool]:
    def mask_key(match: re.Match) -> str:
        # Keep empty lines and the final newline intact for source references.
        return "\n".join(REDACTED if part else "" for part in match.group().split("\n"))

    text, count = _PRIVATE_KEY.subn(mask_key, text)
    lines = text.split("\n")
    for index, line in enumerate(lines):
        starts = [
            match.end() for match in _KEY_PREFIX.finditer(line)
            if match.group("key").lower().replace("-", "_").endswith(_SENSITIVE_SUFFIXES)
        ]
        starts.extend(match.end() for match in _SECRET_FLAG.finditer(line))
        if starts:
            # Conservatively remove the remainder of a sensitive assignment line.
            lines[index] = line[:min(starts)] + REDACTED
            count += 1
    text = "\n".join(lines)
    for pattern, replacement in (
        (_GITHUB_TOKEN, REDACTED),
        (_JWT, REDACTED),
        (_AUTH_VALUE, lambda match: match.group("prefix") + REDACTED),
        (_URL_USERINFO, lambda match: match.group("scheme") + REDACTED + "@"),
    ):
        text, matches = pattern.subn(replacement, text)
        count += matches
    return text, count > 0


def process_log(
    raw: bytes,
    *,
    start_line: int = 1,
    max_lines: int = MAX_OUTPUT_LINES,
    max_bytes: int = MAX_OUTPUT_BYTES,
) -> LogExcerpt:
    """Process complete UTF-8 input before selecting a bounded, numbered excerpt.

    Oversized input is rejected, never sliced before redaction. The future
    downloader must also enforce the input cap while receiving network bytes.
    The output byte cap covers joined UTF-8 text, including separators/markers,
    rather than the metadata envelope. Limits can be reduced but not increased.
    """
    if not isinstance(raw, bytes):
        raise LogProcessingError("Log input must be bytes.")
    if len(raw) > MAX_INPUT_BYTES:
        raise LogProcessingError("Log input exceeds the 1 MiB processing limit.")
    if type(start_line) is not int or start_line < 1:
        raise LogProcessingError("start_line must be a positive integer.")
    if type(max_lines) is not int or not 1 <= max_lines <= MAX_OUTPUT_LINES:
        raise LogProcessingError("max_lines must be an integer from 1 to 100.")
    if type(max_bytes) is not int or not len(TRUNCATED) <= max_bytes <= MAX_OUTPUT_BYTES:
        raise LogProcessingError("max_bytes must be an integer from 11 to 16384.")
    try:
        source = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError:
        raise LogProcessingError("Log input must be valid UTF-8.") from None
    total = len(source.split("\n")) - int(source.endswith("\n")) if source else 0
    if start_line > max(total, 1):
        raise LogProcessingError("start_line exceeds the source log length.")

    # Redact the full bounded source before windowing or clipping a long line.
    processed, redacted = _redact(_strip_controls(source))
    lines = processed.split("\n")[:total]
    chosen: list[LogLine] = []
    used_bytes = 0
    clipped = False
    for index in range(start_line - 1, min(total, start_line - 1 + max_lines)):
        remaining = max_bytes - used_bytes - int(bool(chosen))
        encoded = lines[index].encode("utf-8")
        if len(encoded) > remaining:
            if remaining >= len(TRUNCATED):
                prefix = encoded[:remaining - len(TRUNCATED)].decode("utf-8", errors="ignore")
                chosen.append(LogLine(index + 1, prefix + TRUNCATED))
            clipped = True
            break
        chosen.append(LogLine(index + 1, lines[index]))
        used_bytes += len(encoded) + int(len(chosen) > 1)
    omitted = start_line > 1 or start_line - 1 + len(chosen) < total
    return LogExcerpt(tuple(chosen), total, clipped or omitted, redacted)
