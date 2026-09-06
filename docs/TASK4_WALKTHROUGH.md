# Task 4 Walkthrough - Token-Aware Rate Limiting and Model Fallback

## Purpose

Task 4 implements a tenant-aware LLM routing gateway with:

- a maximum of 50,000 reserved tokens per rolling 60-second window;
- per-tenant accounting derived from Bearer API keys;
- server-side request token calculation;
- persistent on-disk SQLite state;
- atomic concurrent admission decisions;
- a primary model endpoint;
- fallback to a secondary endpoint only when:
  - the primary returns HTTP 429; or
  - the primary exceeds the 3.0-second gateway deadline;
- standardized caller-safe gateway errors.

The implementation is intentionally conservative about both authorization
boundaries and token accounting.

## Architecture

    Client
      |
      | Authorization: Bearer <tenant-key>
      | POST /v1/chat/completions
      v
    +------------------------------------------------+
    | Task 4 Model Gateway                           |
    |                                                |
    |  Bearer extraction                             |
    |          |                                     |
    |          v                                     |
    |  server-side token budgeting                   |
    |          |                                     |
    |          v                                     |
    |  SQLite sliding-window reservation             |
    |          |                                     |
    |       allowed?                                 |
    |       /      \                                 |
    |     no        yes                              |
    |     |          |                               |
    |   HTTP 429     v                               |
    |          primary provider                      |
    |             /       \                          |
    |          success   429 / >3.0 sec              |
    |             |         |                        |
    |             |         v                        |
    |             |    secondary provider            |
    |             |         |                        |
    |             +---------+                        |
    |                   |                            |
    |             safe response/error                |
    +-------------------+----------------------------+
                        |
                        v
                      Client

The default local runtime uses:

    gateway:
        http://127.0.0.1:8020/v1/chat/completions

    synthetic provider host:
        http://127.0.0.1:8021

    primary:
        http://127.0.0.1:8021/primary

    secondary:
        http://127.0.0.1:8021/secondary

Configuration:

    TASK4_DB_PATH
    TASK4_PRIMARY_URL
    TASK4_SECONDARY_URL

## Server-side token budgeting

The caller is not trusted to provide its own authoritative token count.

Allowing this:

    {
        "prompt": "very large request...",
        "token_count": 1
    }

to drive admission would make the limiter trivial to bypass.

Instead, `RequestTokenBudgeter` tokenizes the actual JSON request on the
gateway using `tiktoken` and the `cl100k_base` encoding.

The reservation is:

    input tokens
        +
    requested maximum completion tokens
        =
    reserved tokens

If neither `max_tokens` nor `max_completion_tokens` is supplied, the gateway
uses a bounded default completion reservation.

Both completion-limit fields cannot be specified simultaneously.

The implementation also includes extension fields in token accounting. A
caller therefore cannot hide substantial prompt material inside an otherwise
uncounted custom JSON field.

The tokenizer used here is an assessment implementation detail rather than a
claim that all model providers use identical billing tokenization. A production
gateway should use provider/model-specific accounting where required.

## Reservation before provider execution

The gateway reserves the complete computed token budget before a model request
is started.

This prevents concurrent requests from independently observing available
capacity and then oversubscribing the tenant.

It also means an admitted request retains its reservation when a provider later
fails.

For example, the real runtime validation included a request whose primary
provider returned HTTP 500. Its reserved tokens remained in the active
sliding window.

This is deliberate conservative accounting. It prevents repeated failed
provider requests from becoming an unmetered path around the rate limit.

A production implementation could reconcile reservations against actual
provider usage after completion, but that requires a separate reservation and
settlement lifecycle.

## Per-tenant identity

The HTTP interface accepts:

    Authorization: Bearer <tenant-key>

The raw key is used to select the tenant accounting partition.

Before persistence, the limiter converts it to:

    SHA-256(tenant-key)

The raw tenant key is not written to the SQLite token-event table.

Automated and real runtime tests verify that the raw validation credential
does not appear in the SQLite database files.

For this assessment, the Bearer value provides tenant partitioning. This
implementation does not claim to be a complete production API-key lifecycle
system with issuance, rotation, revocation, or external credential storage.

## SQLite sliding window

Runtime state is stored in a real on-disk SQLite database.

Schema:

    token_events
        id
        tenant_hash
        occurred_at_ms
        token_count

Indexing supports tenant/time-window lookups.

The active window is:

    event time > now - 60 seconds

An event exactly at the cutoff is expired.

Expired events are removed during an admission transaction.

## Why BEGIN IMMEDIATE is important

A naive limiter can race:

    current usage = 45,000

    request A reads 45,000
    request B reads 45,000

    A asks for 4,000
        appears legal

    B asks for 4,000
        also appears legal

    both insert

    actual usage = 53,000

The limiter performs:

    BEGIN IMMEDIATE

before:

    expiration
    usage calculation
    capacity check
    reservation INSERT

The complete admission decision therefore occurs while holding the SQLite
write reservation.

The adversarial concurrency test starts from 45,000 tokens and submits two
4,000-token reservations concurrently.

Observed result:

    one request accepted
    one request rejected
    final persisted usage = 49,000

This demonstrates that concurrent writers cannot oversubscribe the configured
50,000-token budget.

## Exact capacity boundary

The limiter permits usage through exactly:

    50,000 tokens

but rejects any additional reservation while those tokens remain in the
active 60-second window.

Automated testing covers the exact boundary.

A real HTTP runtime validation also calculated an actual request whose
server-computed reservation was exactly:

    input tokens:          18
    completion reserve:   49,982
    total:                50,000

Observed result:

    HTTP status:          200
    provider:             primary
    remaining tokens:     0

An immediately following request produced:

    HTTP status:          429
    provider header:      absent
    remaining tokens:     0
    Retry-After:          60

Caller-visible error:

    {
      "error": {
        "code": "tenant_rate_limit_exceeded",
        "message": "Tenant token budget exceeded"
      }
    }

The second request deliberately requested the synthetic `primary_500`
scenario.

Because it was rejected by the limiter first, the caller did not receive a
primary-provider error.

This demonstrates the ordering:

    rate-limit denial
        occurs before
    provider execution

## Primary routing policy

The gateway does not use unrestricted "try another provider whenever anything
fails" behavior.

The assessment defines two primary fallback conditions:

    primary HTTP 429
    primary operation exceeds 3000 ms

The router implements those conditions directly.

Behavior:

    primary 2xx
        -> return primary result

    primary 429
        -> call secondary

    primary deadline expiry
        -> call secondary

    primary 400
        -> standardized error, no fallback

    primary 401
        -> standardized error, no fallback

    primary 403
        -> standardized error, no fallback

    primary 5xx
        -> standardized error, no fallback

    primary generic transport failure
        -> standardized error, no fallback

This avoids silently widening the failover policy.

Authentication, authorization, malformed-request, policy, or deterministic
provider failures should not automatically create another model-provider call.

## Hard primary deadline

The production router default is:

    3.0 seconds

The timeout surrounds the complete provider coroutine:

    async with asyncio.timeout(3.0):
        await primary.generate(...)

This establishes a gateway wall-clock deadline rather than relying only on
socket-level timeout behavior inside an HTTP client.

The unit tests use a shortened injected timeout for speed while separately
verifying that the production default is exactly 3.0 seconds.

## Real 3-second timeout evidence

The gateway was exercised using independent Uvicorn processes.

The synthetic primary deliberately slept for:

    3.2 seconds

Observed through the real gateway:

    HTTP status:    200
    elapsed:        3.041 seconds
    provider:       secondary

This demonstrates the production-configured 3.0-second fallback behavior over
real HTTP sockets.

## Real HTTP routing evidence

Four requests were sent through the running gateway and synthetic provider
processes.

### Primary success

Observed:

    status:      200
    elapsed:     0.108 seconds
    provider:    primary
    reservation: 117 tokens

### Primary HTTP 429

Observed:

    status:      200
    elapsed:     0.017 seconds
    provider:    secondary
    reservation: 118 tokens

### Primary deadline expiry

Observed:

    status:      200
    elapsed:     3.041 seconds
    provider:    secondary
    reservation: 117 tokens

### Primary HTTP 500

Observed:

    status:      502
    elapsed:     0.010 seconds
    provider:    not exposed
    reservation: 119 tokens

Caller-visible body:

    {
      "error": {
        "code": "primary_provider_rejected",
        "message": "Primary model request failed"
      }
    }

The synthetic upstream body:

    synthetic-internal-primary-detail

did not appear in the caller-visible response.

## Persistent SQLite evidence

The runtime gateway created:

    data/task4-runtime-validation.sqlite3

The file existed on disk during validation.

Four test requests created four token events:

    117
    118
    117
    119

Total:

    471 tokens

The corresponding gateway remaining-capacity headers progressed to:

    49,883
    49,765
    49,648
    49,529

All four database records used the same SHA-256 tenant identifier.

The raw Bearer credential was scanned against the SQLite database files and
was not present.

## Standardized errors

Gateway-controlled failures use:

    {
      "error": {
        "code": "...",
        "message": "..."
      }
    }

Examples include:

    invalid_api_key
    invalid_request
    tenant_rate_limit_exceeded
    rate_limiter_unavailable
    primary_provider_unavailable
    primary_provider_rejected
    fallback_provider_unavailable
    fallback_provider_failed

Raw provider bodies, internal URLs, transport exception strings, SQLite paths,
database exception text, and Python stack traces are not intentionally
returned to the caller.

## Provider abstraction

`ModelProvider` defines the minimal async interface required by the router.

`HTTPModelProvider` converts real HTTP responses into a small internal
`ProviderResponse` containing:

    status code
    content bytes
    content type

Transport exceptions are normalized as `ProviderTransportError`.

The router therefore does not need to know provider hostnames or HTTP-client
exception implementation details.

This also makes timeout and fallback behavior independently testable using
deterministic fake providers.

## Request ordering

The gateway processes a request in this order:

    1. extract tenant credential
    2. validate JSON object
    3. compute trusted token budget
    4. atomically reserve SQLite capacity
    5. route to primary
    6. optionally route to secondary
    7. return provider result or standardized error

A failed request in steps 1-4 does not reach either provider.

Tests explicitly inspect provider call counters to prove that property.

## Rate-limit response headers

Successful and admitted requests include:

    X-RateLimit-Limit-Tokens
    X-RateLimit-Remaining-Tokens

A rate-limited request may also include:

    Retry-After

Successful provider responses additionally include:

    X-Model-Provider

with the generic value:

    primary

or:

    secondary

These labels support assessment evidence without exposing provider network
topology.

## Automated validation

Task 4 tests cover:

- exactly 50,000 tokens accepted;
- additional tokens denied;
- exact 60-second expiration;
- independent tenant windows;
- concurrent oversubscription prevention;
- persistence across limiter instances;
- raw API key absent from database bytes;
- invalid reservation values;
- server-side request token computation;
- completion reservation;
- conflicting completion-limit rejection;
- caller-provided token count not authoritative;
- extension-field token accounting;
- deterministic token budgeting;
- production 3.0-second deadline;
- primary success without fallback;
- primary 429 fallback;
- primary deadline fallback;
- no fallback for 400/401/403/500;
- no fallback for generic primary transport failure;
- sanitized secondary timeout;
- sanitized secondary transport error;
- sanitized secondary non-success body;
- missing tenant credential rejected before provider execution;
- invalid token budget rejected before provider execution;
- rate-limit rejection before provider execution;
- primary upstream body sanitization;
- non-object JSON rejection;
- HTTP-level primary/secondary routing;
- raw tenant key absent from on-disk test database files.

Current repository validation:

    ruff check .
        PASS

    pytest -q
        94 passed

    git diff --check
        PASS

## Files

`src/task4_model_router/auth.py`
- Bearer tenant-key parsing
- basic key-shape validation

`src/task4_model_router/token_budget.py`
- trusted server-side request accounting
- completion reservation
- deterministic canonical JSON tokenization

`src/task4_model_router/limiter.py`
- on-disk SQLite sliding window
- atomic admission transaction
- retry-after calculation
- tenant-key hashing

`src/task4_model_router/provider.py`
- provider protocol
- HTTP provider adapter
- transport-error normalization

`src/task4_model_router/router.py`
- 3.0-second primary deadline
- narrowly scoped 429/timeout fallback
- standardized routing failures

`src/task4_model_router/app.py`
- runnable HTTP gateway
- dependency composition
- caller-safe responses

`src/task4_model_router/mock_provider.py`
- deterministic synthetic primary/secondary provider

`tests/task4/test_limiter.py`
- persistence, boundary, concurrency, and isolation tests

`tests/task4/test_token_budget.py`
- trusted token-accounting tests

`tests/task4/test_router.py`
- deadline, fallback, and error-sanitization tests

`tests/task4/test_app.py`
- composed HTTP gateway tests
- proof denied operations do not reach providers

## Production considerations

A production implementation would additionally require:

- externally managed API-key issuance and revocation;
- credential rotation;
- secret management;
- TLS for all provider connections;
- provider-specific authentication;
- provider/model-specific tokenizer selection;
- reconciliation against actual billed completion usage;
- distributed rate-limiting architecture if multiple gateway nodes must share
  one quota;
- SQLite operational limits assessment before horizontal scaling;
- request-size limits;
- structured audit telemetry;
- metrics and alerting;
- correlation/request identifiers;
- provider health telemetry;
- retry/circuit-breaker policy where explicitly authorized;
- database maintenance and backup policy;
- overload and SQLite busy-state telemetry;
- graceful shutdown behavior;
- client-disconnect handling;
- policy configuration rather than hard-coded thresholds.

SQLite is appropriate for the assessment requirement and provides durable,
transactional local coordination.

For a multi-node production gateway, a centralized transactional or atomic
quota service would likely be preferable.

## Interview explanation

A concise explanation:

> I separated Task 4 into three security decisions: how many tokens a request
> is allowed to reserve, whether that reservation can be admitted atomically,
> and when a second provider is authorized to run. The client does not control
> its own token count. The gateway tokenizes the request and reserves the input
> plus maximum completion budget. SQLite uses BEGIN IMMEDIATE so the
> check-and-reserve sequence is atomic; my concurrency test starts at 45,000
> tokens and races two 4,000-token requests, and exactly one is admitted. For
> routing, I kept fallback deliberately narrow: only primary 429 or the
> three-second gateway deadline invokes the secondary. Other failures are
> normalized instead of widening authority. I then tested the actual HTTP
> gateway with a synthetic provider. The real timeout transitioned to the
> secondary in 3.041 seconds, an exact 50,000-token HTTP reservation succeeded
> with zero remaining capacity, and the next request was rejected before its
> deliberately configured primary-500 path could execute.

## Likely follow-up questions

### Why reserve completion tokens before the model responds?

Without reservation, several concurrent requests can all see free capacity
before their eventual completion usage is known and collectively exceed the
tenant budget. Reservation makes admission conservative and deterministic.

### Why does a provider failure still consume the reservation?

The assessment implementation uses conservative accounting. Releasing failed
requests automatically could allow provider-thrashing traffic to avoid quota
consumption. Production reconciliation can be added as a separate explicit
accounting lifecycle.

### Why hash the tenant API key?

The limiter only requires a stable partition identifier. Persisting the raw
credential creates unnecessary credential exposure.

### Why BEGIN IMMEDIATE?

The usage check and reservation must be serialized. Otherwise concurrent
writers can both observe the same pre-reservation usage and exceed the quota.

### Why not fallback on every primary failure?

Fallback is an authority decision, not merely an availability technique.
The requirement authorizes fallback for primary 429 and timeout. Automatically
falling back after authentication, authorization, malformed-request, policy,
or arbitrary provider failures can create unintended behavior.

### Why use asyncio.timeout instead of only an HTTP client timeout?

It establishes a wall-clock deadline around the complete provider operation.
The gateway therefore owns the 3.0-second routing decision.

### Why SQLite?

The assessment explicitly requires on-disk SQLite. It also provides a useful
transaction boundary for atomic local admission and durable state.

### Would SQLite be your production choice for a large distributed gateway?

Not necessarily. Multiple distributed gateway nodes generally need a shared
atomic quota system. The limiter interface is isolated so the backing store can
be replaced without changing the routing policy.

### Is cl100k_base exactly what every provider bills?

No. It provides deterministic server-side accounting for this assessment.
Production accounting should select the tokenizer appropriate to the specific
provider and model.

### What result best demonstrates correctness?

There are two complementary results. The concurrency test proves that a
45,000-token tenant cannot race two 4,000-token reservations past the limit.
The real HTTP test then proves that an exactly 50,000-token request succeeds
with zero capacity remaining and the next request receives HTTP 429 before
provider routing occurs.
