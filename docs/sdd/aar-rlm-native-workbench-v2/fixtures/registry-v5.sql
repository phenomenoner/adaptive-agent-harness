PRAGMA foreign_keys=OFF;
BEGIN TRANSACTION;
CREATE TABLE asset_bodies (
                body_digest TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                body_json TEXT NOT NULL
            );
INSERT INTO "asset_bodies" VALUES('sha256:4444444444444444444444444444444444444444444444444444444444444444','fixture','fixture.v1','{"value":"fixture"}');
CREATE TABLE asset_events (
                event_digest TEXT PRIMARY KEY,
                event_kind TEXT NOT NULL,
                episode_digest TEXT,
                event_json TEXT NOT NULL
            );
INSERT INTO "asset_events" VALUES('sha256:6666666666666666666666666666666666666666666666666666666666666666','fixture.created',NULL,'{"kind":"fixture.created"}');
CREATE TABLE asset_manifests (
                manifest_digest TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                body_digest TEXT NOT NULL,
                document_json TEXT NOT NULL,
                FOREIGN KEY(body_digest) REFERENCES asset_bodies(body_digest)
            );
INSERT INTO "asset_manifests" VALUES('sha256:5555555555555555555555555555555555555555555555555555555555555555','fixture','sha256:4444444444444444444444444444444444444444444444444444444444444444','{"body_digest":"sha256:4444444444444444444444444444444444444444444444444444444444444444"}');
CREATE TABLE broker_artifacts (
                    digest TEXT PRIMARY KEY,
                    content BLOB NOT NULL
                );
INSERT INTO "broker_artifacts" VALUES('sha256:7777777777777777777777777777777777777777777777777777777777777777',X'666978747572652D6172746966616374');
CREATE TABLE broker_calls (
                operation_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                method TEXT NOT NULL,
                grant_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_digest TEXT NOT NULL,
                request_json TEXT,
                state TEXT NOT NULL,
                response_digest TEXT,
                response_model TEXT NOT NULL,
                response_json TEXT,
                usage_json TEXT NOT NULL,
                failure_code TEXT,
                reconciliation_action TEXT,
                authority_digest TEXT,
                reconciliation_json TEXT,
                compensation_json TEXT,
                reconciled_at_unix_ms INTEGER,
                control_revision INTEGER,
                PRIMARY KEY(operation_id, sequence),
                UNIQUE(operation_id, idempotency_key)
            );
CREATE TABLE broker_children (
                    handle TEXT PRIMARY KEY,
                    receipt_json TEXT NOT NULL
                );
CREATE TABLE operation_attempts (
                operation_id TEXT NOT NULL REFERENCES operations(operation_id),
                attempt_no INTEGER NOT NULL CHECK (attempt_no > 0),
                attempt_id TEXT NOT NULL UNIQUE,
                runtime_generation INTEGER NOT NULL,
                dispatcher_generation INTEGER NOT NULL,
                state TEXT NOT NULL,
                certainty TEXT NOT NULL,
                recovery_reason TEXT,
                created_at_unix_ms INTEGER NOT NULL,
                started_at_unix_ms INTEGER,
                ended_at_unix_ms INTEGER,
                checkpoint_digest TEXT,
                PRIMARY KEY (operation_id, attempt_no)
            );
INSERT INTO "operation_attempts" VALUES('op-v5-populated-fixture',1,'attempt-v5-populated-fixture',1,1,'succeeded','certain',NULL,1999999999100,1999999999200,1999999999900,'sha256:2222222222222222222222222222222222222222222222222222222222222222');
CREATE TABLE operation_checkpoints (
                operation_id TEXT NOT NULL REFERENCES operations(operation_id),
                attempt_no INTEGER NOT NULL,
                checkpoint_digest TEXT NOT NULL,
                checkpoint_kind TEXT NOT NULL,
                artifact_id TEXT,
                media_type TEXT,
                size_bytes INTEGER,
                created_by_operation_id TEXT,
                created_at_unix_ms INTEGER NOT NULL,
                PRIMARY KEY (operation_id, attempt_no, checkpoint_digest),
                FOREIGN KEY (operation_id, attempt_no)
                    REFERENCES operation_attempts(operation_id, attempt_no)
            );
CREATE TABLE operation_controls (
                operation_id TEXT PRIMARY KEY REFERENCES operations(operation_id),
                control_revision INTEGER NOT NULL CHECK (control_revision > 0),
                cancellation_requested INTEGER NOT NULL CHECK (cancellation_requested IN (0, 1)),
                requested_at_unix_ms INTEGER,
                requested_by_digest TEXT,
                reason_code TEXT
            );
CREATE TABLE operation_dispatch (
                operation_id TEXT PRIMARY KEY REFERENCES operations(operation_id),
                kind TEXT NOT NULL,
                state TEXT NOT NULL,
                queued_at_unix_ms INTEGER,
                running_at_unix_ms INTEGER,
                finished_at_unix_ms INTEGER,
                current_attempt_no INTEGER
            );
CREATE TABLE operation_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id TEXT NOT NULL REFERENCES operations(operation_id),
                state TEXT NOT NULL,
                certainty TEXT NOT NULL,
                record_revision INTEGER NOT NULL,
                at_unix_ms INTEGER NOT NULL,
                note TEXT NOT NULL
            , attempt_no INTEGER, event_kind TEXT, payload_json TEXT, payload_digest TEXT);
CREATE TABLE operation_leases (
                operation_id TEXT NOT NULL,
                attempt_no INTEGER NOT NULL,
                lease_epoch INTEGER NOT NULL CHECK (lease_epoch > 0),
                owner_digest TEXT NOT NULL,
                acquired_at_unix_ms INTEGER NOT NULL,
                heartbeat_at_unix_ms INTEGER NOT NULL,
                expires_at_unix_ms INTEGER NOT NULL,
                released_at_unix_ms INTEGER,
                PRIMARY KEY (operation_id, attempt_no, lease_epoch),
                FOREIGN KEY (operation_id, attempt_no)
                    REFERENCES operation_attempts(operation_id, attempt_no)
            );
INSERT INTO "operation_leases" VALUES('op-v5-populated-fixture',1,1,'sha256:3333333333333333333333333333333333333333333333333333333333333333',1999999999200,1999999999800,2000000030000,1999999999900);
CREATE TABLE operation_recovery_decisions (
                operation_id TEXT NOT NULL REFERENCES operations(operation_id),
                decision_no INTEGER NOT NULL CHECK (decision_no > 0),
                prior_attempt_no INTEGER,
                decision TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                input_digest TEXT NOT NULL,
                checkpoint_digest TEXT,
                successor_attempt_no INTEGER,
                created_at_unix_ms INTEGER NOT NULL, policy_version INTEGER, policy_digest TEXT, effect_receipt_digest TEXT, continuation_boundary_digest TEXT,
                PRIMARY KEY (operation_id, decision_no)
            );
CREATE TABLE operation_recovery_policies (
                operation_id TEXT PRIMARY KEY REFERENCES operations(operation_id),
                operation_kind TEXT NOT NULL,
                policy_version INTEGER NOT NULL CHECK (policy_version > 0),
                policy_id TEXT NOT NULL,
                policy_json TEXT NOT NULL,
                policy_digest TEXT NOT NULL,
                environment_digest TEXT NOT NULL,
                created_at_unix_ms INTEGER NOT NULL
            );
CREATE TABLE operation_rlm_boundaries (
                operation_id TEXT NOT NULL,
                prior_attempt_no INTEGER NOT NULL CHECK (prior_attempt_no > 0),
                boundary_digest TEXT NOT NULL,
                boundary_json TEXT NOT NULL,
                created_at_unix_ms INTEGER NOT NULL,
                PRIMARY KEY (operation_id, prior_attempt_no),
                UNIQUE(boundary_digest),
                FOREIGN KEY (operation_id, prior_attempt_no)
                    REFERENCES operation_attempts(operation_id, attempt_no)
            );
CREATE TABLE operation_workspace_boundaries (
                operation_id TEXT NOT NULL,
                prior_attempt_no INTEGER NOT NULL CHECK (prior_attempt_no > 0),
                boundary_digest TEXT NOT NULL,
                boundary_json TEXT NOT NULL,
                created_at_unix_ms INTEGER NOT NULL,
                PRIMARY KEY (operation_id, prior_attempt_no),
                UNIQUE(boundary_digest),
                FOREIGN KEY (operation_id, prior_attempt_no)
                    REFERENCES operation_attempts(operation_id, attempt_no)
            );
CREATE TABLE operation_workspace_checkpoint_selections (
                operation_id TEXT NOT NULL,
                prior_attempt_no INTEGER NOT NULL CHECK (prior_attempt_no > 0),
                selection_digest TEXT NOT NULL,
                selection_json TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('selected', 'restored')),
                restored_handle_json TEXT,
                selected_at_unix_ms INTEGER NOT NULL,
                restored_at_unix_ms INTEGER,
                PRIMARY KEY (operation_id, prior_attempt_no),
                UNIQUE(selection_digest),
                FOREIGN KEY (operation_id, prior_attempt_no)
                    REFERENCES operation_attempts(operation_id, attempt_no)
            );
CREATE TABLE operations (
                operation_id TEXT PRIMARY KEY,
                host_value TEXT NOT NULL,
                principal_value TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                input_digest TEXT NOT NULL,
                state TEXT NOT NULL,
                certainty TEXT NOT NULL,
                runtime_generation INTEGER NOT NULL,
                record_revision INTEGER NOT NULL,
                reconciliation_required INTEGER NOT NULL,
                request_json TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                result_json TEXT,
                failure_json TEXT,
                created_at_unix_ms INTEGER NOT NULL,
                updated_at_unix_ms INTEGER NOT NULL,
                UNIQUE(host_value, principal_value, idempotency_key)
            );
INSERT INTO "operations" VALUES('op-v5-populated-fixture','fixture-host','fixture-principal','fixture-idempotency','sha256:1111111111111111111111111111111111111111111111111111111111111111','succeeded','certain',1,3,0,'{"kind":"fixture-request"}','{"kind":"fixture-payload"}','{"ok":true}',NULL,1999999999000,2000000000000);
CREATE TABLE rlm_jobs (
                operation_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                spec_json TEXT NOT NULL,
                result_json TEXT
            );
INSERT INTO "rlm_jobs" VALUES('op-v5-populated-fixture','succeeded','{"max_steps":1,"query":"fixture","strategy":"baseline"}','{"answer":"fixture"}');
CREATE TABLE rlm_steps (
                operation_id TEXT NOT NULL,
                step_index INTEGER NOT NULL,
                step_json TEXT NOT NULL,
                PRIMARY KEY(operation_id, step_index),
                FOREIGN KEY(operation_id) REFERENCES rlm_jobs(operation_id)
            );
INSERT INTO "rlm_steps" VALUES('op-v5-populated-fixture',0,'{"kind":"evidence.query","status":"succeeded"}');
CREATE TABLE runtime_meta (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                generation INTEGER NOT NULL
            );
INSERT INTO "runtime_meta" VALUES(1,1);
CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at_unix_ms INTEGER NOT NULL,
                migration_digest TEXT NOT NULL
            );
INSERT INTO "schema_migrations" VALUES(1,2000000000000,'sha256:657a31ce6f1bbcbb9ebad063c295e2f1f70e502078677d94acc7dd48141a35d5');
INSERT INTO "schema_migrations" VALUES(2,2000000000000,'sha256:05f57bb0c620a27aac948d2a4c4efc254b049d615828c05c0b7a70c2baf59d7c');
INSERT INTO "schema_migrations" VALUES(3,2000000000000,'sha256:116f5bd9412b4ca8e45214878c505657da3faa745036e6b3d505fe3d07249592');
INSERT INTO "schema_migrations" VALUES(4,2000000000000,'sha256:7273f692c9f1eea5e3f34b455adf98361ccea22195ad5b329c8cea3a67afe94e');
INSERT INTO "schema_migrations" VALUES(5,2000000000000,'sha256:fed8fbd55129f0f73c40f0226113341fec73321823fcb833597e475a1581aa92');
CREATE TABLE supervisor_runs (
                runtime_generation INTEGER PRIMARY KEY,
                dispatcher_generation INTEGER NOT NULL,
                pid INTEGER NOT NULL CHECK (pid > 0),
                process_start_identity TEXT NOT NULL,
                capability_digest TEXT NOT NULL,
                runtime_home_digest TEXT NOT NULL,
                endpoint_kind TEXT NOT NULL,
                discovery_digest TEXT NOT NULL,
                state TEXT NOT NULL,
                started_at_unix_ms INTEGER NOT NULL,
                ready_at_unix_ms INTEGER,
                draining_at_unix_ms INTEGER,
                stopped_at_unix_ms INTEGER,
                terminal_reason TEXT
            );
CREATE TABLE worker_bindings (
                worker_id TEXT PRIMARY KEY,
                runtime_generation INTEGER NOT NULL,
                worker_kind TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                workspace_generation INTEGER NOT NULL CHECK (workspace_generation > 0),
                operation_id TEXT,
                pid INTEGER NOT NULL CHECK (pid > 0),
                process_start_identity TEXT NOT NULL,
                capability_digest TEXT NOT NULL,
                environment_digest TEXT NOT NULL,
                state TEXT NOT NULL,
                started_at_unix_ms INTEGER NOT NULL,
                heartbeat_at_unix_ms INTEGER NOT NULL,
                last_event_sequence INTEGER NOT NULL DEFAULT 0 CHECK (last_event_sequence >= 0),
                ended_at_unix_ms INTEGER,
                termination_receipt_json TEXT
            );
CREATE TABLE workspace_checkpoint_catalog (
                manifest_digest TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                source_generation INTEGER NOT NULL CHECK (source_generation > 0),
                source_revision INTEGER NOT NULL CHECK (source_revision >= 0),
                backend_capability_digest TEXT NOT NULL,
                environment_digest TEXT NOT NULL,
                creation_operation_id TEXT NOT NULL REFERENCES operations(operation_id),
                manifest_json TEXT NOT NULL,
                created_at_unix_ms INTEGER NOT NULL
            );
CREATE TABLE workspace_receipts (
                operation_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                result_json TEXT NOT NULL
            );
INSERT INTO "workspace_receipts" VALUES('op-v5-populated-fixture','workspace-v5-populated-fixture','{"revision":2,"status":"succeeded"}');
CREATE TABLE workspaces (
                workspace_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                generation INTEGER NOT NULL,
                revision INTEGER NOT NULL,
                state_json TEXT NOT NULL
            );
INSERT INTO "workspaces" VALUES('workspace-v5-populated-fixture','session-v5-populated-fixture',1,2,'{"fixture":true}');
CREATE INDEX operation_dispatch_queue_idx
                ON operation_dispatch(state, queued_at_unix_ms, operation_id);
CREATE INDEX operation_attempts_state_idx
                ON operation_attempts(operation_id, state, attempt_no);
CREATE INDEX operation_events_operation_idx
                ON operation_events(operation_id, sequence);
CREATE INDEX supervisor_runs_state_idx
                ON supervisor_runs(state, runtime_generation);
CREATE INDEX worker_bindings_active_idx
                ON worker_bindings(state, runtime_generation, worker_id);
CREATE INDEX worker_bindings_workspace_idx
                ON worker_bindings(workspace_id, workspace_generation, runtime_generation);
CREATE INDEX operation_recovery_policy_kind_idx
                ON operation_recovery_policies(operation_kind, policy_id, policy_version);
CREATE INDEX operation_rlm_boundaries_operation_idx
                ON operation_rlm_boundaries(operation_id, prior_attempt_no);
CREATE INDEX workspace_checkpoint_source_idx
                ON workspace_checkpoint_catalog(
                    workspace_id, source_generation, source_revision, created_at_unix_ms
                );
CREATE INDEX operation_workspace_boundaries_operation_idx
                ON operation_workspace_boundaries(operation_id, prior_attempt_no);
CREATE INDEX operation_workspace_selections_state_idx
                ON operation_workspace_checkpoint_selections(state, operation_id);
CREATE UNIQUE INDEX one_outcome_event_per_episode
            ON asset_events(episode_digest)
            WHERE event_kind = 'outcome';
DELETE FROM "sqlite_sequence";
COMMIT;
PRAGMA foreign_keys=ON;
