# Task 3 Walkthrough - Streaming LLM PII Guardrail

## Purpose

Task 3 implements a streaming LLM gateway that intercepts provider output and
redacts supported personally identifiable information before it reaches the
client.

Supported patterns:

- email addresses;
- U.S. Social Security numbers in `###-##-####` form;
- 13-19 digit credit-card-like values with optional single spaces or hyphens.

Detected values are replaced with:

`[REDACTED]`

The important constraint is that provider output may split a sensitive value
across arbitrary streaming chunks.

## Architecture

    Client
      |
      | POST /v1/chat/completions
      v
    +--------------------------------------+
    | Task 3 Streaming Guardrail Gateway   |
    |                                      |
    | OpenAI-compatible provider adapter   |
    |              |                       |
    |              v                       |
    |       text delta stream              |
    |              |                       |
    |              v                       |
    |       StreamingRedactor              |
    |              |                       |
    |       safe text only                 |
    +--------------+-----------------------+
                   |
                   | SSE
                   v
                 Client

The default local runtime uses:

- guardrail gateway: `127.0.0.1:8010`
- synthetic provider: `127.0.0.1:8011`

The provider URL can be changed with `TASK3_PROVIDER_URL`.

## Why per-chunk regex replacement is insufficient

Provider chunk boundaries do not correspond to semantic or security
boundaries.

For example, an SSN may arrive as:

    chunk 1: 123-45-
    chunk 2: 6789

An email may arrive as:

    chunk 1: alice@exa
    chunk 2: mple.com

Applying a regular expression independently to each chunk would miss both
values.

The implementation therefore keeps a bounded unresolved suffix between
provider events.

## Adaptive unresolved suffix

The redactor does not collect the complete response.

Instead it divides available text into:

    known-safe prefix
        -> emit immediately

    unresolved suffix
        -> retain until additional text establishes whether it is sensitive

For example:

    received:
        "Contact alice@exa"

    emitted:
        "Contact "

    retained:
        "alice@exa"

After the next provider chunk:

    "mple.com,"

the complete email is recognized and the client receives:

    "[REDACTED],"

This preserves progressive delivery without releasing ambiguous text.

## Bounded memory

The unresolved candidate buffer has a hard upper bound based on the maximum
supported candidate span.

The test suite includes a 10,000-character candidate-like input and verifies
that retained state never exceeds the configured maximum.

The gateway therefore does not need memory proportional to the complete LLM
response.

## Overlapping detector regression

Initial adversarial testing found an interaction between the email and
credit-card candidate detectors.

A completed value:

    4111 1111 1111 1111.

could be split by another detector that interpreted the final `1111.` as a
possible future email local part.

Individually, both detectors were behaving according to their own candidate
rules, but their composition created an unsafe emission boundary.

The fix prevents a proposed stream cut from occurring inside any already
complete supported PII match.

A dedicated punctuation-boundary regression test now exercises every possible
two-chunk split of that card value.

This illustrates an important security property:

    individually correct controls
        do not automatically imply
    correct composed behavior

## Provider abstraction

`OpenAICompatibleProvider` consumes an OpenAI-compatible SSE endpoint.

It:

- forces upstream streaming;
- reads provider events asynchronously;
- extracts text deltas;
- recognizes the `[DONE]` sentinel;
- converts invalid or unavailable upstream streams to a sanitized internal
  provider error.

The redaction engine itself has no HTTP or JSON dependency.

That separation keeps transport parsing and security transformation
independently testable.

## SSE output

Safe output is returned as OpenAI-compatible SSE events.

Example:

    data: {"object":"chat.completion.chunk",...}

Successful completion ends with:

    data: [DONE]

A failed upstream stream uses a sanitized SSE error event and does not emit a
successful `[DONE]` sentinel.

## Fail-closed stream failure

If a provider fails while an unresolved suffix is pending, the gateway does
not flush that ambiguous text.

For example:

    provider sends:
        "SSN 123-45-"

    provider then fails

The gateway may release the already-safe prefix:

    "SSN "

but it drops:

    "123-45-"

and returns a sanitized streaming error.

Raw provider hostnames, exception details, and topology are not exposed to the
caller.

## Automated validation

Task 3 tests cover:

- every possible two-chunk split of supported PII samples;
- one-character provider chunks;
- multiple sensitive values in one stream;
- PII at end-of-stream;
- bounded pending memory;
- preservation of non-PII text;
- overlapping-detector regression;
- OpenAI-compatible SSE decoding;
- split PII across provider SSE events;
- progressive output before provider completion;
- fail-closed handling of an interrupted sensitive candidate;
- exception-detail sanitization;
- safe stream ordering.

Current repository validation:

    ruff check .
        PASS

    pytest -q
        54 passed

    git diff --check
        PASS

## Real HTTP streaming validation

The implementation was also exercised using two independent Uvicorn
processes:

    synthetic LLM provider:
        http://127.0.0.1:8011

    guardrail gateway:
        http://127.0.0.1:8010

The synthetic provider deliberately split all three sensitive types across
events.

Observed client stream:

    0.110s  "Contact "
    0.719s  "[REDACTED], SSN "
    1.344s  "[REDACTED], card "
    1.949s  "[REDACTED]. "
    2.562s  "Done."
    2.562s  [DONE]

Measured:

    time to first safe data: 0.110 seconds
    total stream duration:   2.562 seconds

The first safe output therefore arrived roughly 23 times earlier than complete
stream termination.

This demonstrates that the implementation did not solve redaction by buffering
the entire model response.

The original email address, SSN, and card value never appeared in the
client-visible stream.

## Latency tradeoff

A guardrail cannot safely emit a suffix that might still become PII.

Consequently, an ambiguous trailing token may be delayed until the next
provider event or end-of-stream.

For example, the final word `Done.` in the runtime demonstration was retained
until stream completion by the conservative candidate classifier.

This is a deliberate bounded safety/latency tradeoff rather than full-response
buffering.

A production implementation could further tune candidate classification using
provider characteristics and measured latency requirements while retaining the
same no-leak invariant.

## Files

`src/task3_stream_guardrail/redactor.py`
- bounded cross-chunk PII state machine
- supported PII regular expressions
- adaptive safe emission boundaries

`src/task3_stream_guardrail/sse.py`
- OpenAI-compatible SSE encoding and decoding

`src/task3_stream_guardrail/provider.py`
- asynchronous upstream provider adapter
- provider error normalization

`src/task3_stream_guardrail/stream.py`
- provider/redactor/SSE composition
- fail-closed streaming failure behavior

`src/task3_stream_guardrail/app.py`
- runnable guardrail HTTP gateway

`src/task3_stream_guardrail/mock_provider.py`
- deterministic synthetic streaming provider

`tests/task3/test_redactor.py`
- adversarial chunk-boundary and memory tests

`tests/task3/test_streaming.py`
- streaming, failure, and progressive-delivery tests

`tests/task3/test_provider.py`
- provider SSE adapter tests

## Production considerations

A production deployment would additionally require provider-specific
authentication, TLS, secret management, request-size controls, structured
audit telemetry, metrics, correlation identifiers, disconnect handling,
provider-specific protocol compatibility testing, and policy tuning.

Credit-card detection in this assessment is pattern-based. It intentionally
does not claim that every detected numeric sequence represents a valid issued
payment card.

## Interview explanation

A concise explanation:

> The difficult part of streaming redaction is that a provider can split PII
> at any character boundary, so regexing each chunk independently is unsafe. I
> built a bounded stateful redactor that emits only the prefix known to be safe
> and retains an adaptive unresolved suffix between provider events. The
> redactor is separate from the HTTP and SSE layers so I can test the security
> transformation independently. I adversarially split email addresses, SSNs,
> and card values at every possible position and also tested one-character
> streams. One of those tests exposed a composition bug where the email
> candidate detector could split a completed card value, so I added an
> invariant that an emission boundary cannot divide an already complete PII
> match. In the real HTTP demonstration, the first safe data reached the client
> in 110 milliseconds while the provider stream took 2.562 seconds to finish,
> and none of the original sensitive values appeared on the client-visible
> wire.

## Likely follow-up questions

### Why not buffer the entire response?

That would make redaction easier but would defeat streaming and substantially
increase time to first output. The implementation retains only unresolved
candidate state.

### Why not regex every provider chunk independently?

Because provider chunk boundaries can occur inside an email, SSN, or card
value. Security decisions therefore require state across events.

### Why drop pending data when the provider fails?

An interrupted suffix may be sensitive but incomplete. Flushing it because the
provider failed would turn an availability failure into a possible
confidentiality failure.

### Why use a mock provider?

It provides deterministic arbitrary chunk boundaries and repeatable timing
without external credentials or cost. The gateway itself consumes an
OpenAI-compatible streaming interface rather than a mock-specific Python
function.

### Does the implementation identify real payment cards?

It detects card-like numeric patterns required by the assessment. It does not
claim issuer validity and intentionally does not require Luhn validation.

### What was the most important bug found during development?

Independent candidate detectors interacted in a way that could split a
completed credit-card value before redaction. The adversarial test suite caught
the composition failure, and the fix now prevents stream boundaries from
dividing complete sensitive matches.
