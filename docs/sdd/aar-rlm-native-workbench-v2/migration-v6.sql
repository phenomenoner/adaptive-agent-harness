PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS migration_v6_attestations (
    migration_version INTEGER PRIMARY KEY CHECK (migration_version = 6),
    attestation_digest TEXT NOT NULL UNIQUE,
    cutover_epoch TEXT NOT NULL UNIQUE,
    snapshot_id TEXT NOT NULL UNIQUE,
    snapshot_sha256 TEXT NOT NULL,
    snapshot_size_bytes INTEGER NOT NULL CHECK (snapshot_size_bytes > 0),
    canonical_v5_row_set_digest TEXT NOT NULL,
    source_commit TEXT NOT NULL,
    wheel_digest TEXT NOT NULL,
    profile_digest TEXT NOT NULL,
    skill_digest TEXT NOT NULL,
    contract_manifest_digest TEXT NOT NULL,
    migration_sql_digest TEXT NOT NULL,
    external_authority_store_id TEXT NOT NULL,
    external_authority_prepared_digest TEXT NOT NULL,
    started_at_unix_ms INTEGER NOT NULL,
    completed_at_unix_ms INTEGER NOT NULL,
    foreign_key_violation_count INTEGER NOT NULL
        CHECK (foreign_key_violation_count = 0),
    integrity_result TEXT NOT NULL CHECK (integrity_result = 'ok'),
    CHECK (completed_at_unix_ms >= started_at_unix_ms)
);

CREATE TABLE IF NOT EXISTS rlm_workbench_jobs (
    operation_id TEXT PRIMARY KEY REFERENCES operations(operation_id),
    phase TEXT NOT NULL CHECK (phase IN (
        'accepted', 'preparing_workspace', 'running', 'waiting_external',
        'checkpointing', 'finalizing', 'succeeded', 'failed', 'cancelled',
        'timed_out', 'indeterminate', 'parked'
    )),
    control_revision INTEGER NOT NULL CHECK (control_revision > 0),
    cancellation_revision INTEGER NOT NULL CHECK (cancellation_revision >= 0),
    cancellation_requested INTEGER NOT NULL CHECK (cancellation_requested IN (0, 1)),
    spec_json TEXT NOT NULL,
    spec_digest TEXT NOT NULL,
    context_digest TEXT NOT NULL,
    route_binding_digest TEXT NOT NULL,
    cumulative_deadline_unix_ms INTEGER NOT NULL,
    workspace_id TEXT,
    workspace_generation INTEGER CHECK (workspace_generation IS NULL OR workspace_generation > 0),
    workspace_revision INTEGER CHECK (workspace_revision IS NULL OR workspace_revision >= 0),
    checkpoint_digest TEXT,
    result_json TEXT,
    result_digest TEXT,
    failure_json TEXT,
    certainty TEXT NOT NULL CHECK (certainty IN ('certain', 'indeterminate')),
    created_at_unix_ms INTEGER NOT NULL,
    updated_at_unix_ms INTEGER NOT NULL,
    CHECK ((cancellation_requested = 1) = (cancellation_revision > 0)),
    CHECK ((phase = 'succeeded') = (result_json IS NOT NULL)),
    CHECK (result_json IS NULL OR result_digest IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS rlm_workbench_cells (
    operation_id TEXT NOT NULL REFERENCES rlm_workbench_jobs(operation_id),
    cell_execution_id TEXT NOT NULL UNIQUE,
    cell_index INTEGER NOT NULL CHECK (cell_index >= 0),
    source_json TEXT NOT NULL,
    source_digest TEXT NOT NULL,
    pre_checkpoint_digest TEXT,
    post_checkpoint_digest TEXT,
    state TEXT NOT NULL CHECK (state IN (
        'prepared', 'running', 'suspended', 'lost_before_commit',
        'committed', 'failed', 'cancelled', 'discarded'
    )),
    attempt_id TEXT REFERENCES operation_attempts(attempt_id),
    attempt_fence TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    workspace_generation INTEGER NOT NULL CHECK (workspace_generation > 0),
    pre_workspace_revision INTEGER NOT NULL CHECK (pre_workspace_revision >= 0),
    post_workspace_revision INTEGER CHECK (post_workspace_revision IS NULL OR post_workspace_revision >= 0),
    result_json TEXT,
    result_digest TEXT,
    created_at_unix_ms INTEGER NOT NULL,
    updated_at_unix_ms INTEGER NOT NULL,
    PRIMARY KEY (operation_id, cell_index),
    CHECK (post_workspace_revision IS NULL OR post_workspace_revision > pre_workspace_revision)
);

CREATE TABLE IF NOT EXISTS rlm_workbench_suspensions (
    operation_id TEXT NOT NULL REFERENCES rlm_workbench_jobs(operation_id),
    suspension_revision INTEGER NOT NULL CHECK (suspension_revision > 0),
    control_revision INTEGER NOT NULL CHECK (control_revision > 0),
    cell_execution_id TEXT REFERENCES rlm_workbench_cells(cell_execution_id),
    logical_owner_json TEXT NOT NULL,
    logical_owner_digest TEXT NOT NULL,
    broker_method TEXT NOT NULL CHECK (broker_method IN (
        'model.request', 'subagent.submit', 'subagent.result',
        'evidence.query', 'effect.propose'
    )),
    contract_id TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    ticket_id TEXT NOT NULL UNIQUE,
    checkpoint_digest TEXT,
    state TEXT NOT NULL CHECK (state IN ('pending', 'settled', 'cancelled', 'parked')),
    created_at_unix_ms INTEGER NOT NULL,
    settled_at_unix_ms INTEGER,
    UNIQUE (
        operation_id, suspension_revision, ticket_id, broker_method,
        contract_id, request_digest, logical_owner_json
    ),
    PRIMARY KEY (operation_id, suspension_revision)
);

CREATE TABLE IF NOT EXISTS caller_work_tickets (
    ticket_id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL REFERENCES rlm_workbench_jobs(operation_id),
    suspension_revision INTEGER NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 0),
    ticket_digest TEXT NOT NULL,
    request_json TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    method TEXT NOT NULL CHECK (method IN (
        'model.request', 'subagent.submit', 'subagent.result',
        'evidence.query', 'effect.propose'
    )),
    contract_id TEXT NOT NULL,
    logical_owner_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN (
        'pending', 'send_reserved', 'send_started', 'settled_success',
        'settled_failure', 'outcome_unknown', 'cancel_requested',
        'cancelled_before_send', 'cancelled_certain', 'quarantined'
    )),
    deadline_unix_ms INTEGER NOT NULL,
    claimant_principal_id TEXT,
    claimant_session_id TEXT,
    adapter_id TEXT,
    adapter_generation INTEGER,
    claim_id TEXT,
    claim_fence TEXT,
    claim_expires_at_unix_ms INTEGER,
    physical_attempt_id TEXT,
    external_idempotency_key TEXT,
    lookup_supported INTEGER CHECK (lookup_supported IS NULL OR lookup_supported IN (0, 1)),
    cancel_supported INTEGER CHECK (cancel_supported IS NULL OR cancel_supported IN (0, 1)),
    send_started_at_unix_ms INTEGER,
    sent_request_digest TEXT,
    sent_at_unix_ms INTEGER,
    provider_or_child_request_id TEXT,
    settled_receipt_digest TEXT,
    settled_at_unix_ms INTEGER,
    created_at_unix_ms INTEGER NOT NULL,
    updated_at_unix_ms INTEGER NOT NULL,
    UNIQUE (operation_id, suspension_revision),
    UNIQUE (physical_attempt_id),
    UNIQUE (ticket_id, physical_attempt_id),
    FOREIGN KEY (operation_id, suspension_revision)
        REFERENCES rlm_workbench_suspensions(operation_id, suspension_revision),
    FOREIGN KEY (
        operation_id, suspension_revision, ticket_id, method,
        contract_id, request_digest, logical_owner_json
    ) REFERENCES rlm_workbench_suspensions(
        operation_id, suspension_revision, ticket_id, broker_method,
        contract_id, request_digest, logical_owner_json
    ),
    CHECK ((state NOT IN ('pending', 'cancelled_before_send')) <= (
        claimant_principal_id IS NOT NULL AND claimant_session_id IS NOT NULL
        AND adapter_id IS NOT NULL AND adapter_generation IS NOT NULL
        AND claim_id IS NOT NULL AND claim_fence IS NOT NULL
        AND claim_expires_at_unix_ms IS NOT NULL
        AND physical_attempt_id IS NOT NULL AND external_idempotency_key IS NOT NULL
        AND lookup_supported IS NOT NULL AND cancel_supported IS NOT NULL
    )),
    CHECK ((state IN ('send_started', 'outcome_unknown', 'settled_success',
                      'settled_failure', 'cancel_requested', 'cancelled_certain', 'quarantined'))
           <= (send_started_at_unix_ms IS NOT NULL AND sent_request_digest IS NOT NULL)),
    CHECK ((state = 'pending') <= (
        claimant_principal_id IS NULL AND claimant_session_id IS NULL
        AND adapter_id IS NULL AND adapter_generation IS NULL
        AND claim_id IS NULL AND claim_fence IS NULL
        AND claim_expires_at_unix_ms IS NULL AND physical_attempt_id IS NULL
        AND external_idempotency_key IS NULL
        AND lookup_supported IS NULL AND cancel_supported IS NULL
        AND send_started_at_unix_ms IS NULL AND sent_request_digest IS NULL
        AND sent_at_unix_ms IS NULL AND provider_or_child_request_id IS NULL
        AND settled_receipt_digest IS NULL AND settled_at_unix_ms IS NULL
    )),
    CHECK ((state = 'send_reserved') <= (
        claimant_principal_id IS NOT NULL AND claimant_session_id IS NOT NULL
        AND adapter_id IS NOT NULL AND adapter_generation IS NOT NULL
        AND claim_id IS NOT NULL AND claim_fence IS NOT NULL
        AND claim_expires_at_unix_ms IS NOT NULL AND physical_attempt_id IS NOT NULL
        AND external_idempotency_key IS NOT NULL
        AND lookup_supported IS NOT NULL AND cancel_supported IS NOT NULL
        AND send_started_at_unix_ms IS NULL AND sent_request_digest IS NULL
        AND sent_at_unix_ms IS NULL AND provider_or_child_request_id IS NULL
    )),
    CHECK ((state = 'cancelled_before_send') <= (
        send_started_at_unix_ms IS NULL AND sent_request_digest IS NULL
        AND sent_at_unix_ms IS NULL AND provider_or_child_request_id IS NULL
        AND settled_receipt_digest IS NOT NULL AND settled_at_unix_ms IS NOT NULL
        AND (
            (
                claimant_principal_id IS NULL AND claimant_session_id IS NULL
                AND adapter_id IS NULL AND adapter_generation IS NULL
                AND claim_id IS NULL AND claim_fence IS NULL
                AND claim_expires_at_unix_ms IS NULL AND physical_attempt_id IS NULL
                AND external_idempotency_key IS NULL
                AND lookup_supported IS NULL AND cancel_supported IS NULL
            ) OR (
                claimant_principal_id IS NOT NULL AND claimant_session_id IS NOT NULL
                AND adapter_id IS NOT NULL AND adapter_generation IS NOT NULL
                AND claim_id IS NOT NULL AND claim_fence IS NOT NULL
                AND claim_expires_at_unix_ms IS NOT NULL AND physical_attempt_id IS NOT NULL
                AND external_idempotency_key IS NOT NULL
                AND lookup_supported IS NOT NULL AND cancel_supported IS NOT NULL
            )
        )
    )),
    CHECK ((state NOT IN ('settled_success', 'settled_failure',
                          'cancelled_before_send', 'cancelled_certain')) <= (
        settled_receipt_digest IS NULL AND settled_at_unix_ms IS NULL
    )),
    CHECK ((state = 'settled_success') <= (sent_at_unix_ms IS NOT NULL)),
    CHECK ((state IN ('settled_success', 'settled_failure',
                      'cancelled_before_send', 'cancelled_certain')) <= (
        settled_receipt_digest IS NOT NULL AND settled_at_unix_ms IS NOT NULL
    ))
);

CREATE TABLE IF NOT EXISTS caller_work_candidate_receipts (
    ticket_id TEXT NOT NULL,
    physical_attempt_id TEXT NOT NULL,
    receipt_digest TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK (source_kind IN (
        'claimant_callback', 'provider_lookup', 'child_lookup',
        'cancellation_ack', 'reconciler'
    )),
    observed_at_unix_ms INTEGER NOT NULL,
    PRIMARY KEY (ticket_id, physical_attempt_id, receipt_digest),
    FOREIGN KEY (ticket_id, physical_attempt_id)
        REFERENCES caller_work_tickets(ticket_id, physical_attempt_id)
);

CREATE TABLE IF NOT EXISTS rlm_workbench_successor_outbox (
    operation_id TEXT NOT NULL REFERENCES rlm_workbench_jobs(operation_id),
    suspension_revision INTEGER NOT NULL,
    settlement_digest TEXT NOT NULL,
    outbox_digest TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK (state IN ('pending', 'prepared', 'consumed')),
    rebind_generation INTEGER NOT NULL DEFAULT 0 CHECK (rebind_generation >= 0),
    successor_attempt_id TEXT REFERENCES operation_attempts(attempt_id),
    successor_attempt_fence TEXT,
    created_at_unix_ms INTEGER NOT NULL,
    prepared_at_unix_ms INTEGER,
    consumed_at_unix_ms INTEGER,
    PRIMARY KEY (operation_id, suspension_revision),
    UNIQUE (operation_id, suspension_revision, outbox_digest),
    UNIQUE (operation_id, suspension_revision, outbox_digest, settlement_digest),
    FOREIGN KEY (operation_id, suspension_revision)
        REFERENCES rlm_workbench_suspensions(operation_id, suspension_revision),
    CHECK ((state = 'pending') <= (
        successor_attempt_id IS NULL AND successor_attempt_fence IS NULL
        AND prepared_at_unix_ms IS NULL AND consumed_at_unix_ms IS NULL
    )),
    CHECK ((state = 'prepared') <= (
        rebind_generation > 0
        AND successor_attempt_id IS NOT NULL AND successor_attempt_fence IS NOT NULL
        AND prepared_at_unix_ms IS NOT NULL AND consumed_at_unix_ms IS NULL
    )),
    CHECK ((state = 'consumed') <= (
        rebind_generation > 0
        AND successor_attempt_id IS NOT NULL AND successor_attempt_fence IS NOT NULL
        AND prepared_at_unix_ms IS NOT NULL AND consumed_at_unix_ms IS NOT NULL
    ))
);

CREATE TABLE IF NOT EXISTS rlm_workbench_attempt_authority (
    operation_id TEXT PRIMARY KEY REFERENCES rlm_workbench_jobs(operation_id),
    attempt_id TEXT NOT NULL UNIQUE REFERENCES operation_attempts(attempt_id),
    attempt_fence TEXT NOT NULL,
    authority_generation INTEGER NOT NULL CHECK (authority_generation > 0),
    worker_owner_generation INTEGER NOT NULL CHECK (worker_owner_generation > 0),
    worker_process_identity_digest TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    workspace_generation INTEGER NOT NULL CHECK (workspace_generation > 0),
    workspace_revision INTEGER NOT NULL CHECK (workspace_revision >= 0),
    cell_execution_id TEXT NOT NULL REFERENCES rlm_workbench_cells(cell_execution_id),
    rebind_token_digest TEXT UNIQUE REFERENCES rlm_workbench_rebind_transfers(token_digest),
    updated_at_unix_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS rlm_workbench_rebind_transfers (
    token_digest TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL REFERENCES rlm_workbench_jobs(operation_id),
    suspension_revision INTEGER NOT NULL,
    settlement_digest TEXT NOT NULL,
    outbox_digest TEXT NOT NULL,
    rebind_generation INTEGER NOT NULL CHECK (rebind_generation > 0),
    prior_authority_generation INTEGER NOT NULL CHECK (prior_authority_generation > 0),
    successor_authority_generation INTEGER NOT NULL CHECK (successor_authority_generation > 0),
    expected_control_revision INTEGER NOT NULL CHECK (expected_control_revision > 0),
    expected_cancellation_revision INTEGER NOT NULL CHECK (expected_cancellation_revision >= 0),
    expected_cumulative_deadline_unix_ms INTEGER NOT NULL,
    prior_attempt_id TEXT NOT NULL REFERENCES operation_attempts(attempt_id),
    prior_attempt_fence TEXT NOT NULL,
    successor_attempt_id TEXT NOT NULL REFERENCES operation_attempts(attempt_id),
    successor_attempt_fence TEXT NOT NULL,
    worker_owner_generation INTEGER NOT NULL CHECK (worker_owner_generation > 0),
    worker_process_identity_digest TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    workspace_generation INTEGER NOT NULL CHECK (workspace_generation > 0),
    workspace_revision INTEGER NOT NULL CHECK (workspace_revision >= 0),
    cell_execution_id TEXT NOT NULL REFERENCES rlm_workbench_cells(cell_execution_id),
    expires_at_unix_ms INTEGER NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('prepared', 'committed', 'aborted', 'consumed')),
    consumption_kind TEXT CHECK (
        consumption_kind IS NULL
        OR consumption_kind IN ('worker_ack', 'recovery_fenced_loss')
    ),
    ack_digest TEXT,
    prepared_at_unix_ms INTEGER NOT NULL,
    committed_at_unix_ms INTEGER,
    aborted_at_unix_ms INTEGER,
    consumed_at_unix_ms INTEGER,
    UNIQUE (operation_id, suspension_revision, rebind_generation),
    FOREIGN KEY (operation_id, suspension_revision)
        REFERENCES rlm_workbench_suspensions(operation_id, suspension_revision),
    FOREIGN KEY (operation_id, suspension_revision, outbox_digest, settlement_digest)
        REFERENCES rlm_workbench_successor_outbox(
            operation_id, suspension_revision, outbox_digest, settlement_digest
        ),
    CHECK (successor_authority_generation > prior_authority_generation),
    CHECK (expires_at_unix_ms > prepared_at_unix_ms),
    CHECK ((state = 'prepared') <= (
        committed_at_unix_ms IS NULL AND aborted_at_unix_ms IS NULL
        AND consumed_at_unix_ms IS NULL AND consumption_kind IS NULL
        AND ack_digest IS NULL
    )),
    CHECK ((state = 'committed') <= (
        committed_at_unix_ms IS NOT NULL AND aborted_at_unix_ms IS NULL
        AND consumed_at_unix_ms IS NULL AND consumption_kind IS NULL
        AND ack_digest IS NULL
    )),
    CHECK ((state = 'aborted') <= (
        committed_at_unix_ms IS NULL AND aborted_at_unix_ms IS NOT NULL
        AND consumed_at_unix_ms IS NULL AND consumption_kind IS NULL
        AND ack_digest IS NULL
    )),
    CHECK ((state = 'consumed') <= (
        committed_at_unix_ms IS NOT NULL AND aborted_at_unix_ms IS NULL
        AND consumed_at_unix_ms IS NOT NULL AND consumption_kind IS NOT NULL
    )),
    CHECK ((consumption_kind = 'worker_ack') <= (ack_digest IS NOT NULL)),
    CHECK ((consumption_kind = 'recovery_fenced_loss') <= (ack_digest IS NULL))
);

CREATE TABLE IF NOT EXISTS rlm_workbench_artifact_stages (
    stage_id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL REFERENCES rlm_workbench_jobs(operation_id),
    cell_execution_id TEXT NOT NULL REFERENCES rlm_workbench_cells(cell_execution_id),
    logical_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('result', 'evidence', 'diagnostic', 'intermediate')),
    media_type TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    content BLOB NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('staged', 'committed', 'discarded')),
    created_at_unix_ms INTEGER NOT NULL,
    updated_at_unix_ms INTEGER NOT NULL,
    UNIQUE (operation_id, cell_execution_id, logical_name),
    CHECK (length(content) = size_bytes)
);

CREATE TABLE IF NOT EXISTS rlm_workbench_cell_manifests (
    operation_id TEXT NOT NULL REFERENCES rlm_workbench_jobs(operation_id),
    cell_execution_id TEXT NOT NULL REFERENCES rlm_workbench_cells(cell_execution_id),
    manifest_json TEXT NOT NULL,
    manifest_digest TEXT NOT NULL,
    created_at_unix_ms INTEGER NOT NULL,
    PRIMARY KEY (operation_id, cell_execution_id)
);

CREATE TABLE IF NOT EXISTS rlm_workbench_finalization_manifests (
    operation_id TEXT PRIMARY KEY REFERENCES rlm_workbench_jobs(operation_id),
    finalizer_attempt_id TEXT NOT NULL REFERENCES operation_attempts(attempt_id),
    finalizer_attempt_fence TEXT NOT NULL,
    expected_prior_phase TEXT NOT NULL CHECK (expected_prior_phase = 'finalizing'),
    expected_cancellation_revision INTEGER NOT NULL CHECK (expected_cancellation_revision >= 0),
    expected_cumulative_deadline_unix_ms INTEGER NOT NULL,
    control_revision INTEGER NOT NULL CHECK (control_revision > 0),
    unresolved_required_ticket_count INTEGER NOT NULL CHECK (unresolved_required_ticket_count = 0),
    proposal_digest TEXT NOT NULL,
    result_digest TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    manifest_digest TEXT NOT NULL,
    committed_at_unix_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS broker_contract_catalog_v2 (
    method TEXT NOT NULL,
    contract_id TEXT NOT NULL,
    request_schema_digest TEXT NOT NULL,
    response_schema_digest TEXT NOT NULL,
    PRIMARY KEY (method, contract_id, request_schema_digest, response_schema_digest)
);

CREATE TABLE IF NOT EXISTS broker_backend_availability_v2 (
    method TEXT NOT NULL,
    contract_id TEXT NOT NULL,
    request_schema_digest TEXT NOT NULL,
    response_schema_digest TEXT NOT NULL,
    backend_kind TEXT NOT NULL CHECK (backend_kind IN (
        'caller_driver', 'native', 'reference', 'unconfigured'
    )),
    configured INTEGER NOT NULL CHECK (configured IN (0, 1)),
    reference_only INTEGER NOT NULL CHECK (reference_only IN (0, 1)),
    adapter_id TEXT,
    adapter_generation INTEGER,
    evidence_tier TEXT NOT NULL CHECK (evidence_tier IN (
        'unknown', 'caller_observed', 'host_receipt_bound', 'provider_attested'
    )),
    updated_at_unix_ms INTEGER NOT NULL,
    PRIMARY KEY (method, contract_id, request_schema_digest, response_schema_digest),
    FOREIGN KEY (method, contract_id, request_schema_digest, response_schema_digest)
        REFERENCES broker_contract_catalog_v2(
            method, contract_id, request_schema_digest, response_schema_digest
        ),
    CHECK ((backend_kind = 'unconfigured') <= (
        configured = 0 AND reference_only = 0
        AND adapter_id IS NULL AND adapter_generation IS NULL
        AND evidence_tier = 'unknown'
    )),
    CHECK ((backend_kind = 'reference') <= (
        configured = 1 AND reference_only = 1
        AND adapter_id IS NULL AND adapter_generation IS NULL
        AND evidence_tier = 'unknown'
    )),
    CHECK ((backend_kind IN ('caller_driver', 'native')) <= (
        configured = 1 AND reference_only = 0
        AND adapter_id IS NOT NULL AND adapter_generation IS NOT NULL
    ))
);

CREATE INDEX IF NOT EXISTS idx_workbench_phase_deadline
    ON rlm_workbench_jobs(phase, cumulative_deadline_unix_ms);
CREATE INDEX IF NOT EXISTS idx_workbench_cells_state
    ON rlm_workbench_cells(operation_id, state, cell_index);
CREATE INDEX IF NOT EXISTS idx_caller_work_state_deadline
    ON caller_work_tickets(state, deadline_unix_ms);
CREATE INDEX IF NOT EXISTS idx_caller_work_claim_expiry
    ON caller_work_tickets(state, claim_expires_at_unix_ms);
CREATE INDEX IF NOT EXISTS idx_candidate_receipts_ticket
    ON caller_work_candidate_receipts(ticket_id, observed_at_unix_ms);
CREATE INDEX IF NOT EXISTS idx_artifact_stages_state
    ON rlm_workbench_artifact_stages(operation_id, state);
CREATE INDEX IF NOT EXISTS idx_successor_outbox_state
    ON rlm_workbench_successor_outbox(state, created_at_unix_ms);
