# Traceability matrix

**Generated from the tests by `trace_matrix.py`. Do not edit.** Read the
[requirements](requirements.md) with it.

It shows which tests are **tagged** with each requirement. It does not show that they
passed: for that, make a record from a test run, as the
[test scripts](test-scripts.md) describe.
Nor does a tag show that the tests are enough for your use. The deployment duties have
no tests here: they are yours to test.

52 requirements, 185 tagged tests. A test may cover several requirements, and may run with several inputs.

## REQ-001

Import planted errors from files its users write, and refuse one whose content does not fit the queue's item schema or whose defect field is not a property of that schema.

- [`tests/test_planted.py`](../tests/test_planted.py)
  - `test_bad_input_is_refused_with_a_clear_message`
  - `test_every_schema_problem_is_reported_at_once`
  - `test_import_from_a_file`
  - `test_import_stores_an_unapproved_planted_error`

## REQ-002

Give each planted error its own target ID in a reserved range, refuse a second import of the same content, and refuse to change a planted error by importing its ID again.

- [`tests/test_planted.py`](../tests/test_planted.py)
  - `test_an_id_cannot_be_reused_to_change_content`
  - `test_reserved_target_ids_are_unique`
  - `test_the_same_payload_cannot_be_imported_twice`

## REQ-003

Use a planted error only after approval by someone other than its author: by two different people on a queue marked GxP, by one otherwise. Names are compared ignoring case and surrounding spaces.

- [`tests/test_planted.py`](../tests/test_planted.py)
  - `test_a_gxp_queue_needs_two_approvers`
  - `test_a_non_gxp_queue_needs_one_approver`
  - `test_authors_cannot_approve_their_own`
  - `test_one_person_cannot_approve_twice`

## REQ-004

Refuse changes to a planted error's content after import. Record approvals and retirement once each, with who did it, and refuse deletion.

- [`tests/test_planted.py`](../tests/test_planted.py)
  - `test_retirement_is_permanent_and_attributed`
- [`tests/test_requirement_gaps.py`](../tests/test_requirement_gaps.py)
  - `test_every_column_of_a_planted_error_is_accounted_for`
  - `test_no_part_of_a_planted_errors_content_can_be_changed`
- [`tests/test_schema.py`](../tests/test_schema.py)
  - `test_approvals_and_retirement_cannot_be_rewritten`
  - `test_planted_error_content_is_fixed`
  - `test_planted_errors_cannot_be_replaced_or_deleted`

## REQ-005

Never create a planted error from an item submitted for review. Optional drafting helpers work only from an item the user supplies.

- [`tests/test_mutators.py`](../tests/test_mutators.py)
  - `test_a_draft_shares_nothing_with_its_source`
- [`tests/test_requirement_gaps.py`](../tests/test_requirement_gaps.py)
  - `test_only_the_import_of_a_users_file_adds_to_the_library`
  - `test_reviewing_submitted_items_never_creates_a_planted_error`

## REQ-006

With each real item submitted, release a planted error with probability p / (1 − p) for the queue's planted share p, each draw independent of the others, using the operating system's secure random source unless the host's code supplies another.

- [`tests/test_gate.py`](../tests/test_gate.py)
  - `test_planted_share_is_within_a_fifth_of_a_point`
  - `test_the_default_random_source_is_unpredictable`
  - `test_the_due_probability_accounts_for_the_planted_items_themselves`
  - `test_there_is_no_pattern`

## REQ-007

Release only approved, unretired planted errors for live use that still fit the item schema; never one with a copy awaiting a decision; never one the named reviewer has been given, shown or has decided on; and choose among the least used.

- [`tests/test_gate.py`](../tests/test_gate.py)
  - `test_a_named_reviewer_does_not_get_the_same_one_twice`
  - `test_being_assigned_counts_even_if_someone_else_decided`
  - `test_having_met_it_counts_without_an_assignment`
  - `test_least_used_go_first`
  - `test_no_second_copy_while_one_awaits_a_decision`
  - `test_only_approved_planted_errors_are_used`
- [`tests/test_planted.py`](../tests/test_planted.py)
  - `test_a_schema_change_holds_back_planted_errors_that_no_longer_fit`
- [`tests/test_qualification.py`](../tests/test_qualification.py)
  - `test_the_two_pools_never_mix`

## REQ-008

When a planted error is due and none can be used, release none, record the shortfall and not make it up later.

- [`tests/test_gate.py`](../tests/test_gate.py)
  - `test_each_shortfall_is_recorded_and_nothing_is_saved_up`

## REQ-009

Accept a real item even if the planted-error side fails or the item does not match its schema, and record a warning or error that names no planted error. If either database is unavailable, refuse the submission.

- [`tests/test_api.py`](../tests/test_api.py)
  - `test_a_damaged_library_does_not_hold_up_real_work`
  - `test_a_locked_log_does_not_use_up_planted_errors`
  - `test_a_lost_registry_stops_submission_and_leaves_no_item`
  - `test_an_unusable_schema_stops_planting_but_not_real_work`
  - `test_real_work_that_breaks_the_schema_is_accepted_with_a_warning`

## REQ-010

Return to the host, for each item, only an opaque token and the item's content: no marker of its kind and no target ID.

- [`tests/test_api.py`](../tests/test_api.py)
  - `test_real_and_planted_tickets_have_the_same_shape`
  - `test_the_target_id_is_kept_in_the_registry_only`
- [`tests/test_requirement_gaps.py`](../tests/test_requirement_gaps.py)
  - `test_a_ticket_holds_a_token_and_the_content_and_nothing_else`

## REQ-011

Treat real and planted items alike in everything the review application sees: token form, field order, the random order of a pair, and the same refusals with the same messages.

- [`tests/test_api.py`](../tests/test_api.py)
  - `test_both_kinds_are_refused_for_the_same_reasons`
  - `test_field_order_does_not_show_which_item_is_planted`
  - `test_neither_order_gives_the_planted_item_away`

## REQ-012

Before an item is decided, show nothing in `oversight.db`, in its errors or in its log messages that tells whether the item is planted. Its logs and errors shall not name a planted error.

- [`tests/gate1/test_planted_never_executes.py`](../tests/gate1/test_planted_never_executes.py)
  - `test_logs_and_errors_never_name_a_planted_error`
- [`tests/test_api.py`](../tests/test_api.py)
  - `test_nothing_in_the_host_logs_names_a_planted_error`
  - `test_real_and_planted_rows_and_events_have_the_same_shape`
- [`tests/test_schema.py`](../tests/test_schema.py)
  - `test_oversight_items_cannot_show_which_are_planted`

## REQ-013

Never pass a planted item to the host's write function, whatever was decided about it.

- [`tests/gate1/test_planted_never_executes.py`](../tests/gate1/test_planted_never_executes.py)
  - `test_a_planted_error_edited_and_approved_is_not_executed`
  - `test_a_rubber_stamping_reviewer_cannot_get_a_planted_error_executed`

## REQ-014

Call the write function only when all of these hold: the item has one decision, approve or edit; the restricted database says it is real and agrees with what the log committed to at submission; the content is exactly what was approved or recorded as the edit; the target ID is not in the reserved range; and execution has not been requested before.

- [`tests/gate1/test_planted_never_executes.py`](../tests/gate1/test_planted_never_executes.py)
  - `test_a_changed_payload_is_refused_for_either_kind`
  - `test_a_second_forged_decision_is_caught`
  - `test_an_edit_executes_only_the_edited_payload`
  - `test_an_undecided_item_of_either_kind_is_not_executed`
  - `test_even_with_the_whole_log_rewritten_the_reserved_id_stops_it`
  - `test_nothing_runs_twice`
  - `test_relabelling_a_planted_error_as_real_is_caught`

## REQ-015

Execute nothing when any of those checks fails or cannot be completed, including when a database is unavailable or damaged.

- [`tests/gate1/test_planted_never_executes.py`](../tests/gate1/test_planted_never_executes.py)
  - `test_a_database_failing_at_any_statement`
  - `test_when_even_the_block_cannot_be_logged_nothing_runs`
  - `test_with_the_registry_down_nothing_runs`

## REQ-016

Call the write function at most once for an item, including under simultaneous requests and after a crash or a failed write, and never retry it.

- [`tests/gate1/test_planted_never_executes.py`](../tests/gate1/test_planted_never_executes.py)
  - `test_a_failing_executor_is_logged_and_not_retried`
  - `test_after_a_crash_during_execution_the_guard_does_not_retry`
  - `test_concurrent_execution_requests_run_a_real_item_once`
  - `test_nothing_runs_twice`

## REQ-017

Not use up an item's one attempt when a request is refused before execution is requested, for example during an outage.

- [`tests/gate1/test_paused_agents.py`](../tests/gate1/test_paused_agents.py)
  - `test_settle_refuses_both_kinds_for_the_same_reasons`
- [`tests/gate1/test_planted_never_executes.py`](../tests/gate1/test_planted_never_executes.py)
  - `test_after_an_outage_the_real_item_can_run_and_the_planted_one_still_cannot`
  - `test_an_edit_executes_only_the_edited_payload`

## REQ-018

Report with a distinct error when the write function ran but its result could not be recorded.

- [`tests/gate1/test_planted_never_executes.py`](../tests/gate1/test_planted_never_executes.py)
  - `test_if_the_result_cannot_be_logged_the_caller_is_told_it_ran`

## REQ-019

Refuse a real item whose target ID is in the reserved range, or whose content contains its target ID.

- [`tests/test_api.py`](../tests/test_api.py)
  - `test_a_reserved_target_id_is_refused`
  - `test_the_payload_must_not_hold_the_target_id`

## REQ-020

Record exactly one decision for an item, only from a reviewer for whom it has recorded that the item was shown, identified by the ID the host supplies.

- [`tests/test_api.py`](../tests/test_api.py)
  - `test_a_reviewer_needs_a_clean_id`
  - `test_only_one_decision`
  - `test_the_decider_must_have_been_shown_it`

## REQ-021

For an edit, work out itself which top-level fields changed, and refuse an edit that changes nothing.

- [`tests/test_api.py`](../tests/test_api.py)
  - `test_a_removed_field_counts_as_changed`
  - `test_reordering_and_number_spelling_are_not_edits`
  - `test_reviewproof_works_out_what_changed`

## REQ-022

Record each showing of an item and, when the host supplies it, the names of what was on screen, without their content.

- [`tests/test_api.py`](../tests/test_api.py)
  - `test_showing_twice_and_to_two_people_is_recorded`
- [`tests/test_capture.py`](../tests/test_capture.py)
  - `test_a_manifest_is_completed_with_empty_lists`
  - `test_context_is_stored_per_showing`

## REQ-023

For real items submitted for review, store no item content and no token, only salted hashes. (Planted errors and qualification items are stored in full in the restricted database.)

- [`tests/test_api.py`](../tests/test_api.py)
  - `test_no_item_content_or_token_is_stored`
- [`tests/test_capture.py`](../tests/test_capture.py)
  - `test_only_ids_types_times_and_hashes_are_kept`
- [`tests/test_requirement_gaps.py`](../tests/test_requirement_gaps.py)
  - `test_no_database_holds_real_content_or_a_token`

## REQ-024

Add every event to a hash-chained log that its database refuses to update, delete from or replace in.

- [`tests/test_schema.py`](../tests/test_schema.py)
  - `test_an_insert_must_extend_the_chain`
  - `test_events_cannot_be_replaced`
  - `test_events_cannot_be_updated_or_deleted`
  - `test_upsert_cannot_change_an_event`

## REQ-025

Record for each event its type, UTC time, AI system version, and, where they apply, queue, item, reviewer and decision.

- [`tests/test_eventlog.py`](../tests/test_eventlog.py)
  - `test_the_hash_follows_the_documented_format`
- [`tests/test_requirement_gaps.py`](../tests/test_requirement_gaps.py)
  - `test_every_event_of_a_whole_workflow_says_when_what_and_who`
- [`tests/test_schema.py`](../tests/test_schema.py)
  - `test_malformed_events_are_refused`

## REQ-026

On verification, report any changed, removed or reordered event; and, given a head exported earlier, also a log whose latest events were removed or that was rebuilt.

- [`tests/test_verify.py`](../tests/test_verify.py)
  - `test_a_removed_event_is_reported`
  - `test_a_rewritten_log_needs_the_exported_head`
  - `test_any_edit_is_reported_at_that_event`
  - `test_removing_the_latest_events_needs_the_exported_head`
  - `test_swapped_events_are_reported`

## REQ-027

Commit in the log, for each item, to whether it is real or planted, its target ID and any planted error behind it with its approvals, and report on verification any later change to them.

- [`tests/test_verify.py`](../tests/test_verify.py)
  - `test_a_changed_registry_row_is_detected`
  - `test_a_planted_error_changed_after_use_is_detected`
  - `test_a_real_item_relabelled_as_planted_is_detected`

## REQ-028

Verify without changing either database.

- [`tests/test_verify.py`](../tests/test_verify.py)
  - `test_a_readonly_connection_cannot_write`
  - `test_verifying_does_not_change_the_databases`

## REQ-029

Stop simultaneous writers from splitting the chain. An operation that cannot get the write lock within its time limit shall be refused and add nothing to the log.

- [`tests/test_eventlog.py`](../tests/test_eventlog.py)
  - `test_a_locked_log_raises_store_error`
  - `test_a_transaction_commits_or_rolls_back_as_a_whole`
  - `test_concurrent_processes_cannot_fork_the_chain`
  - `test_concurrent_threads_cannot_fork_the_chain`
- [`tests/test_requirement_gaps.py`](../tests/test_requirement_gaps.py)
  - `test_a_refused_write_adds_nothing_to_the_log`

## REQ-030

Tie each item to the AI system version in force when it was submitted (model, model version, prompt version, tool schema), record each change, including a return to an earlier version, and flag the affected reviewers for requalification.

- [`tests/test_context_changes.py`](../tests/test_context_changes.py)
  - `test_a_prompt_version_bump_flags_the_systems_reviewers`
  - `test_each_identifying_change_counts`
  - `test_going_back_to_an_earlier_context_is_also_a_change`
  - `test_new_items_get_the_new_context_and_old_ones_keep_theirs`

## REQ-031

Record a qualification session's pass criteria before it starts, require at least 10 planted errors and 5 correct items, and refuse criteria that cannot fail a reviewer who answers every item the same way.

- [`tests/test_qualification.py`](../tests/test_qualification.py)
  - `test_a_session_needs_ten_defects_and_no_repeats_in_the_list`
  - `test_answering_everything_the_same_way_fails`
  - `test_bad_plans`
  - `test_the_pass_mark_cannot_change_mid_session`

## REQ-032

Keep qualification planted errors separate from live ones, never give a reviewer one from an earlier session of theirs, and never execute a session item or count it in live metrics.

- [`tests/test_qualification.py`](../tests/test_qualification.py)
  - `test_a_session_never_executes_or_enters_the_metrics`
  - `test_abandoning_counts_as_not_passed_and_uses_the_items`
  - `test_one_open_session_and_new_items_for_a_retake`
  - `test_the_two_pools_never_mix`
- [`tests/test_requirement_gaps.py`](../tests/test_requirement_gaps.py)
  - `test_a_session_item_cannot_be_reviewed_or_executed_as_live_work`

## REQ-033

On verification with the restricted database, check that session items, answers and results are unchanged, and work each result out again.

- [`tests/test_qualification.py`](../tests/test_qualification.py)
  - `test_a_changed_answer_key_is_detected`
  - `test_a_changed_judgment_is_detected`
  - `test_a_result_that_the_answers_do_not_support_is_detected`

## REQ-034

Report a reviewer as qualified for a queue only if they passed a session that started after the queue's latest change of AI system version. (It reports this and does not stop anyone reviewing.)

- [`tests/test_qualification.py`](../tests/test_qualification.py)
  - `test_a_change_during_a_session_means_it_does_not_count`
  - `test_status_follows_the_ai_system_version`

## REQ-035

Show scores only when the evidence verifies. Otherwise show none and say why.

- [`tests/test_cli_commands.py`](../tests/test_cli_commands.py)
  - `test_a_failed_check_replaces_the_report_rather_than_leaving_an_old_one`
- [`tests/test_report.py`](../tests/test_report.py)
  - `test_a_relabelled_item_gives_no_scores`
  - `test_a_tampered_log_gives_no_scores`

## REQ-036

Work out catch rate as planted errors caught out of planted errors decided at the decider's first sight, leaving undecided ones out and reporting repeats separately, with a 95% Wilson interval.

- [`tests/test_checks.py`](../tests/test_checks.py)
  - `test_repeats_do_not_count`
- [`tests/test_golden.py`](../tests/test_golden.py)
  - `test_each_metric_matches_the_hand_value`
  - `test_undecided_items`
- [`tests/test_stats.py`](../tests/test_stats.py)
  - `test_wilson_matches_published_values_to_four_places`

## REQ-037

Score a planted error as caught only if it is rejected, or edited in its defect field.

- [`tests/test_feedback.py`](../tests/test_feedback.py)
  - `test_an_edit_that_misses_the_defect_is_reported_as_a_miss`
  - `test_scoring`

## REQ-038

Work out fast approvals, review time, agreement, workload and fatigue as the command reference defines them.

- [`tests/test_checks.py`](../tests/test_checks.py)
  - `test_a_long_pause_starts_a_new_session`
  - `test_exactly_the_minimum_time_is_not_fast`
  - `test_fatigue_alerts_at_a_20_point_drop`
  - `test_rejections_are_not_fast_approvals`
  - `test_sessions_are_per_person_and_cross_queues`
  - `test_the_middle_item_of_an_odd_session_counts_as_early`
  - `test_weekly_fast_approval_limit`
- [`tests/test_golden.py`](../tests/test_golden.py)
  - `test_each_decided_item_matches_the_hand_built_table`
  - `test_each_metric_matches_the_hand_value`
  - `test_every_scenario_item_is_projected`

## REQ-039

Judge each threshold as breached, met or not enough data, and never present not enough data as met.

- [`tests/test_checks.py`](../tests/test_checks.py)
  - `test_classes_without_enough_data_are_not_judged`
  - `test_no_data_is_never_a_pass`

## REQ-040

Show individual results only when asked, and only from at least 10 first-sight planted errors, a minimum settings can raise but not lower; withhold a group figure that rests on one reviewer with fewer; and send alerts only to the QA lead, with queue-level findings only.

- [`tests/test_alerts.py`](../tests/test_alerts.py)
  - `test_email_to_the_qa_lead`
  - `test_the_message_is_about_the_queue`
- [`tests/test_checks.py`](../tests/test_checks.py)
  - `test_evaluate_runs_every_rule_and_names_no_one`
- [`tests/test_report.py`](../tests/test_report.py)
  - `test_individual_results_need_ten_planted_errors`
  - `test_no_reviewer_appears_unless_individual_results_are_asked_for`
  - `test_one_reviewers_group_score_is_withheld_like_an_individual_one`
- [`tests/test_reporting_config.py`](../tests/test_reporting_config.py)
  - `test_bad_routes`
  - `test_the_individual_minimum_can_only_be_raised`

## REQ-041

Name no item and no planted error in a report.

- [`tests/test_report.py`](../tests/test_report.py)
  - `test_nothing_in_a_report_names_an_item_or_a_planted_error`

## REQ-042

Give a reviewer feedback only on planted errors they decided, once each, at the time the queue's setting allows.

- [`tests/test_feedback.py`](../tests/test_feedback.py)
  - `test_a_batched_queue_waits_for_the_batch`
  - `test_each_reviewer_gets_their_own_in_decision_order`
  - `test_feedback_on_a_planted_error_only_and_only_once`

## REQ-043

Make no network connection except to send alerts, to the routes in its settings, when asked to send them. It shall send no usage data.

- [`tests/test_alerts.py`](../tests/test_alerts.py)
  - `test_without_a_route_nothing_is_sent`
- [`tests/test_cli_commands.py`](../tests/test_cli_commands.py)
  - `test_nothing_is_sent_when_nothing_is_wrong`
- [`tests/test_requirement_gaps.py`](../tests/test_requirement_gaps.py)
  - `test_a_whole_workflow_uses_no_network`
  - `test_only_the_alerts_module_can_use_the_network`
  - `test_the_network_block_would_see_a_connection`

## REQ-044

Refuse invalid settings and say what is wrong, including a reserved target prefix too weak to protect.

- [`tests/test_config.py`](../tests/test_config.py)
  - `test_invalid_values_are_rejected`
  - `test_missing_keys_are_rejected`
  - `test_weak_reserved_prefix_is_rejected`

## REQ-045

Never create a database just by opening one, and never create a new database beside an existing one of the pair.

- [`tests/test_api.py`](../tests/test_api.py)
  - `test_a_missing_log_is_not_replaced`
  - `test_initialise_is_repeatable_but_never_replaces_one_lost_file`
  - `test_opening_never_creates_the_databases`

## REQ-046

Change a database's schema only when asked to upgrade it, copy the log exactly or undo the whole upgrade, and refuse a database made by a newer version.

- [`tests/test_schema.py`](../tests/test_schema.py)
  - `test_a_newer_schema_is_refused`
- [`tests/test_upgrade.py`](../tests/test_upgrade.py)
  - `test_a_copy_that_is_not_exact_undoes_the_upgrade`
  - `test_nothing_upgrades_a_database_unasked`
  - `test_the_log_is_unchanged_by_the_upgrade`

## REQ-047

When a submission is repeated with the same key, target, content and reviewer, return the same tickets, record nothing and not draw again; and refuse the key for anything else.

- [`tests/gate1/test_paused_agents.py`](../tests/gate1/test_paused_agents.py)
  - `test_a_key_cannot_be_used_for_anything_else`
  - `test_repeating_a_key_returns_the_same_tickets_and_draws_once`

## REQ-048

Run a paused call's write only through the guard, so that no recorded approval or edit of that content means no write, and so that a decision on a planted item releases nothing.

- [`tests/gate1/test_paused_agents.py`](../tests/gate1/test_paused_agents.py)
  - `test_a_planted_payload_cannot_run_under_the_real_approval`
  - `test_a_resume_without_approval_runs_nothing`
  - `test_approving_only_the_planted_item_lets_nothing_run`

## REQ-049

Through each adapter, put no ticket token and no planted content into the agent's messages or saved state.

- [`tests/conformance.py`](../tests/conformance.py) (shared: runs once for each of 6 adapter test files)
  - `test_nothing_planted_and_no_token_is_in_the_agents_state`
  - `test_nothing_planted_is_in_the_agents_state_after_it_resumes_either`

## REQ-050

Through each adapter, write nothing because of what the agent library says: not on a forced or repeated resume, a standing approval or a replayed pause.

- [`tests/conformance.py`](../tests/conformance.py) (shared: runs once for each of 6 adapter test files)
  - `test_a_forged_resume_writes_nothing`
  - `test_restoring_a_pause_and_resuming_again_writes_once`
  - `test_running_the_tool_without_a_pause_writes_nothing`
- [`tests/test_adapter_agent_framework.py`](../tests/test_adapter_agent_framework.py) (needs Microsoft Agent Framework)
  - `test_a_standing_approval_cannot_write_what_was_not_reviewed`
- [`tests/test_adapter_openai_agents.py`](../tests/test_adapter_openai_agents.py) (needs OpenAI Agents SDK)
  - `test_a_standing_approval_cannot_write_what_was_not_reviewed`

## REQ-051

Through each adapter, end the agent's run when an approved write fails, without giving the model the chance to try it again.

- [`tests/conformance.py`](../tests/conformance.py) (shared: runs once for each of 6 adapter test files)
  - `test_a_failed_write_stops_the_run`
- [`tests/test_adapter_agent_framework.py`](../tests/test_adapter_agent_framework.py) (needs Microsoft Agent Framework)
  - `test_no_failure_reaches_the_model`
- [`tests/test_adapter_openai_agents.py`](../tests/test_adapter_openai_agents.py) (needs OpenAI Agents SDK)
  - `test_a_write_cancelled_from_inside_stops_the_run`
- [`tests/test_adapter_pydantic_ai.py`](../tests/test_adapter_pydantic_ai.py) (needs Pydantic AI)
  - `test_no_error_from_a_write_reaches_the_model`
- [`tests/test_adapter_strands.py`](../tests/test_adapter_strands.py) (needs Strands Agents)
  - `test_no_failure_reaches_the_model_or_is_lost`

## REQ-052

Through each adapter, run each of several calls proposed at once no more than once.

- [`tests/conformance.py`](../tests/conformance.py) (shared: runs once for each of 6 adapter test files)
  - `test_two_calls_decided_before_one_resume_each_run_once`
  - `test_two_calls_proposed_at_once_each_run_once`
