"""Bounded-memory streaming PII redaction for Task 3.

The redactor emits text as soon as it is known to be safe while retaining only
a bounded trailing suffix that could become part of an email address, SSN, or
credit-card number when the next provider chunk arrives.
"""

from __future__ import annotations

import re
import string

REPLACEMENT = "[REDACTED]"

MAX_EMAIL_LOCAL_CHARS = 64

# A deliberately bounded candidate window. Valid email addresses are bounded,
# so retaining an unlimited response tail is unnecessary.
MAX_EMAIL_SPAN_CHARS = 320

# Nineteen digits plus up to eighteen single separators.
MAX_CARD_SPAN_CHARS = 37

MAX_PENDING_CHARS = MAX_EMAIL_SPAN_CHARS

EMAIL_LOCAL_CHARS = frozenset(
    string.ascii_letters
    + string.digits
    + ".!#$%&'*+/=?^_`{|}~-"
)

EMAIL_DOMAIN_CHARS = frozenset(
    string.ascii_letters
    + string.digits
    + ".-"
)

EMAIL_CANDIDATE_CHARS = EMAIL_LOCAL_CHARS | {"@"}

EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9.!#$%&'*+/=?^_`{|}~-])"
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}"
    r"@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+"
    r"(?![A-Za-z0-9.-])"
)

SSN_RE = re.compile(
    r"(?<![0-9])"
    r"[0-9]{3}-[0-9]{2}-[0-9]{4}"
    r"(?![0-9])"
)

CARD_RE = re.compile(
    r"(?<![0-9])"
    r"(?:[0-9][ -]?){12,18}[0-9]"
    r"(?![0-9])"
)

_SSN_MASK = "ddd-dd-dddd"


def _redact_complete(text: str) -> str:
    """Redact complete supported PII values from text with known boundaries."""

    text = EMAIL_RE.sub(REPLACEMENT, text)
    text = SSN_RE.sub(REPLACEMENT, text)
    text = CARD_RE.sub(REPLACEMENT, text)
    return text


def _extend_cut_over_complete_pii(text: str, cut: int) -> int:
    """Prevent an emission boundary from splitting a complete PII match.

    The adaptive suffix classifiers can overlap. For example, the final digits
    of a completed credit-card value may also resemble a possible future email
    local part. If that secondary candidate proposes a cut inside an already
    complete PII value, move the cut to the end of the complete match so the
    entire value reaches the redaction pass together.
    """

    extended = cut

    while True:
        previous = extended

        for pattern in (EMAIL_RE, SSN_RE, CARD_RE):
            for match in pattern.finditer(text):
                if match.start() < extended < match.end():
                    extended = match.end()

        if extended == previous:
            return extended


def _trailing_run_start(
    text: str,
    allowed: frozenset[str],
    limit: int,
) -> int:
    """Return the bounded start of the trailing run made from allowed chars."""

    end = len(text)
    start = end

    while (
        start > 0
        and end - start < limit
        and text[start - 1] in allowed
    ):
        start -= 1

    return start


def _email_candidate_start(text: str) -> int | None:
    """Return the start of a trailing suffix that could become an email."""

    end = len(text)

    run_start = _trailing_run_start(
        text,
        EMAIL_CANDIDATE_CHARS,
        MAX_EMAIL_SPAN_CHARS,
    )

    if run_start == end:
        return None

    run = text[run_start:end]

    if "@" in run:
        at_offset = run.rfind("@")
        at_index = run_start + at_offset

        local_start = at_index

        while (
            local_start > run_start
            and at_index - local_start < MAX_EMAIL_LOCAL_CHARS
            and text[local_start - 1] in EMAIL_LOCAL_CHARS
        ):
            local_start -= 1

        domain = text[at_index + 1 : end]

        if (
            local_start < at_index
            and all(char in EMAIL_DOMAIN_CHARS for char in domain)
        ):
            return local_start

        return None

    # No @ has arrived yet. A bounded trailing local-part token may become an
    # email when a future chunk begins with '@'.
    local_start = end

    while (
        local_start > run_start
        and end - local_start < MAX_EMAIL_LOCAL_CHARS
        and text[local_start - 1] in EMAIL_LOCAL_CHARS
    ):
        local_start -= 1

    if local_start < end:
        return local_start

    return None


def _is_ssn_prefix(candidate: str) -> bool:
    """Return True when candidate is a prefix of ###-##-####."""

    if not candidate or len(candidate) > len(_SSN_MASK):
        return False

    for position, char in enumerate(candidate):
        expected = _SSN_MASK[position]

        if expected == "d":
            if char not in string.digits:
                return False
        elif char != expected:
            return False

    return True


def _ssn_candidate_start(text: str) -> int | None:
    """Return the earliest trailing suffix that could become an SSN."""

    candidate_start: int | None = None

    max_length = min(len(_SSN_MASK), len(text))

    for length in range(1, max_length + 1):
        suffix = text[-length:]

        if _is_ssn_prefix(suffix):
            candidate_start = len(text) - length

    return candidate_start


def _card_candidate_start(text: str) -> int | None:
    """Return the start of a trailing possible 13-19 digit card value."""

    end = len(text)
    start = end

    allowed = string.digits + " -"

    while (
        start > 0
        and end - start < MAX_CARD_SPAN_CHARS
        and text[start - 1] in allowed
    ):
        start -= 1

    run = text[start:end]

    if not run or not any(char in string.digits for char in run):
        return None

    leading = 0

    while leading < len(run) and run[leading] in " -":
        leading += 1

    candidate = run[leading:]

    if not candidate or candidate[0] not in string.digits:
        return None

    digit_count = sum(
        char in string.digits
        for char in candidate
    )

    if not 1 <= digit_count <= 19:
        return None

    if re.search(r"[ -]{2}", candidate):
        return None

    return start + leading


def _unsafe_suffix_start(text: str) -> int:
    """Find the earliest unresolved trailing PII candidate."""

    starts = (
        _email_candidate_start(text),
        _ssn_candidate_start(text),
        _card_candidate_start(text),
    )

    resolved = [
        start
        for start in starts
        if start is not None
    ]

    if not resolved:
        return len(text)

    return min(resolved)


class StreamingRedactor:
    """Redact PII across arbitrary provider chunk boundaries.

    `feed()` returns only text that is safe to release immediately.
    `flush()` must be called once the provider stream ends.
    """

    def __init__(self) -> None:
        self._pending = ""

    @property
    def pending_size(self) -> int:
        """Number of unresolved characters currently retained."""

        return len(self._pending)

    def feed(self, text: str) -> str:
        """Consume one provider text chunk and return its safe output."""

        if not text:
            return ""

        self._pending += text

        cut = _unsafe_suffix_start(self._pending)
        cut = _extend_cut_over_complete_pii(self._pending, cut)

        safe = self._pending[:cut]
        self._pending = self._pending[cut:]

        # Enforce a hard memory bound even if unexpected input reaches the
        # candidate classifier. Anything older than the supported maximum PII
        # span cannot belong to a supported future match.
        if len(self._pending) > MAX_PENDING_CHARS:
            overflow = len(self._pending) - MAX_PENDING_CHARS
            safe += self._pending[:overflow]
            self._pending = self._pending[overflow:]

        return _redact_complete(safe)

    def flush(self) -> str:
        """Resolve and return the final retained suffix at end-of-stream."""

        final = _redact_complete(self._pending)
        self._pending = ""
        return final
