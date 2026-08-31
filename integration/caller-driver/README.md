# Caller-driver conformance v1

This integration surface standardizes the **pre-send** handoff between an AAR caller-work ticket and
a host-owned local relay. It is provider-neutral: AAR still receives no provider credential, owns no
provider SDK, and performs no physical provider request.

The public wire value is `aar.caller-driver-ready.v1`, implemented by
`aar.caller_driver_models.CallerDriverReadyEnvelope` and packaged as
`integration/caller-driver/aar-caller-driver-ready-v1.schema.json`. A relay emits exactly one
canonical JSON object
followed by one LF byte:

```json
{"adapter_generation":1,"adapter_id":"host-caller-driver-v1","base_url":"http://127.0.0.1:43123","event":"ready","launch_nonce":"launch-nonce-001","physical_attempt_id":"attempt-001","request_digest":"sha256:0000000000000000000000000000000000000000000000000000000000000000","schema_version":"aar.caller-driver-ready.v1","ticket_digest":"sha256:1111111111111111111111111111111111111111111111111111111111111111","ticket_id":"ticket-001"}
```

`base_url` is deliberately narrow: explicit loopback HTTP host and port, no user information, query,
fragment, or endpoint path. The launch nonce is non-secret process-binding material supplied by the
parent. Credentials and request bodies must never enter this envelope.

## Required order

Before calling `aar_broker_work_mark_send_started`, the host must complete every fallible local
preparation step:

1. resolve and validate all executable and output paths using the host's trusted workspace root;
2. start the exact relay process;
3. receive and parse the canonical v1 readiness line;
4. bind launch nonce, adapter identity/generation, ticket ID/digest, request digest, and physical
   attempt ID to the exact current `send_reserved` ticket;
5. construct the provider client and immutable request payload without sending it.

Only then may the host cross the durable boundary:

```text
ready and locally prepared
-> mark_send_started returns the exact send_started successor
-> invoke one physical-send callback
```

`aar.integrations.caller_driver.CallerDriverSendGuard` is the synchronous reference helper for this
sequence. It rejects the legacy `{ready, host, port}` shape and every binding mismatch before the mark
callback. It never calls the physical-send callback unless the mark callback returns the exact
one-revision `send_started` successor. It blocks a second invocation after success or failure.

An async host may implement an equivalent helper, but it must preserve the same wire contract,
binding checks, ordering, no-replay rule, and negative test matrix.

## Failure semantics

- Path, startup, readiness, client-construction, or payload-preparation failure is pre-send. Do not
  mark the ticket; cancel or reconcile only while authoritative AAR state still proves certain
  no-send.
- A mark mutation that does not return an exact authenticated `send_started` successor must not be
  followed by a provider call. Read authoritative status and reconcile; do not infer no-send from a
  lost host response.
- Any exception after the mark and during the physical-send callback is `outcome_unknown` from AAR's
  authority boundary. Do not recreate the guard or blindly retry.
- Process restart after a successful mark has no replay contract. The durable ticket remains the
  authority; use lookup/cancel/reconcile evidence permitted by the admitted operation.

## What this proves—and does not

The checked-in tests exercise the exact model, canonical newline frame, a real subprocess stdout
boundary, ticket/attempt binding, mark-before-send ordering, pre-send rejection, mark-receipt
rejection, outcome-unknown classification, and one-shot replay blocking with **zero provider calls**.

This does **not** qualify any host adapter, provider route, credential resolver, provider SDK,
timeout/retry policy, complete response transport, settlement path, installed wheel, or live runtime.
Those remain separate host-owned T2/T3/T4 evidence gates.
