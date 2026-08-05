"""Enumerations accepted by the platform API, mirrored so the CLI can reject bad input locally.

Each tuple matches a validator in the merged ``mod_api`` blueprint. Keeping them
here means a wrong ``--status`` fails instantly with a Click usage error instead
of costing a round trip and coming back as an HTTP 400.
"""

#: ``GET /runs`` — derived from the latest ``TestProgress`` row. pass/fail are
#: per-sample outcomes, not run states, so they are deliberately absent.
RUN_STATUSES = ('queued', 'running', 'canceled')

#: ``GET /runs/{id}/samples`` and ``GET /samples/{id}/history`` (``_VALID_SAMPLE_STATUSES``).
SAMPLE_STATUSES = ('pass', 'fail', 'missing_output', 'not_started')

#: ``GET /system/queue`` — a queued run is one with no running progress row yet.
QUEUE_STATUSES = ('queued', 'running')

#: ``GET /samples`` — catalog visibility, unrelated to test outcome.
SAMPLE_CATALOG_STATUSES = ('active', 'inactive')

#: ``TestPlatform`` values accepted by every ``?platform`` filter.
PLATFORMS = ('linux', 'windows')

#: ``TokenCreateRequestSchema.expires_in_days`` validates ``Range(min=1, max=30)``.
TOKEN_MIN_DAYS = 1
TOKEN_MAX_DAYS = 30

#: ``mod_api.models.api_token.VALID_SCOPES``. Omitting scopes at login grants the
#: server's default set (``runs:read`` + ``results:read``), which is why
#: ``--scope`` is optional. ``system:write`` is deliberately separate from
#: ``system:read`` so a monitoring token can watch the platform without being
#: able to reconfigure it — every ``sp admin`` write command needs it.
TOKEN_SCOPES = ('runs:read', 'runs:write', 'results:read', 'baselines:write',
                'system:read', 'system:write', 'tokens:manage')

#: ``TokenCreateRequestSchema.scopes`` validates ``Length(max=len(VALID_SCOPES))``,
#: so asking for everything you are allowed can never fail validation.
TOKEN_MAX_SCOPES = len(TOKEN_SCOPES)

#: ``POST /runs/{id}/cancel`` rejects a reason shorter than this.
CANCEL_REASON_MIN_LENGTH = 5

#: ``mod_api.middleware.validation._parse_limit`` 400s on ``limit < 1 or
#: limit > 100``, for both the offset and cursor paginators. This is the real
#: ceiling on every ``--limit``; a service-level clamp behind it is unreachable.
MAX_PAGE_LIMIT = 100

#: ``validate_offset_pagination`` rejects a negative offset and anything above
#: this (a signed 32-bit maximum).
MAX_OFFSET = 2147483647

#: ``GET /runs/{id}/errors`` — the types ``derive_errors_for_run`` can emit.
#: Test errors are derived from result rows, not stored, so this is the closed set.
ERROR_TYPES = ('exit_code_mismatch', 'missing_output', 'diff_mismatch')

#: ``GET /runs/{id}/infrastructure-errors`` — ``_classify_infra_error`` keyword buckets.
INFRA_ERROR_TYPES = ('vm_provisioning', 'checkout', 'merge', 'build', 'worker', 'storage')

#: ``mod_api.services.error_service._SEVERITY_ORDER``. Test errors are only ever
#: ``error`` or ``warning``; infrastructure errors are always ``critical``.
ERROR_SEVERITIES = ('info', 'warning', 'error', 'critical')

#: ``GET /runs/{id}/error-summary`` — anything else is a 400.
ERROR_GROUP_BY = ('type', 'severity', 'sample_id', 'regression_id')

#: ``GET /runs/{id}/artifacts`` — the ``?type`` values ``list_artifacts`` builds.
ARTIFACT_TYPES = ('binary', 'coredump', 'combined_stdout', 'build_log',
                  'expected_output', 'actual_output')

#: ``GET /runs/{id}/logs`` — ``_extract_level`` scans raw lines for these, so a
#: line matching none of them is reported as ``info``.
LOG_LEVELS = ('critical', 'error', 'warning', 'info', 'debug')

#: ``GET /runs/{id}/logs`` — ``_extract_source`` keywords; unmatched lines are ``web``.
LOG_SOURCES = ('orchestrator', 'worker', 'build', 'test_runner', 'web')

#: ``GET /runs/{id}/logs`` rejects a longer ``contains`` filter with a 400.
LOG_CONTAINS_MAX_LENGTH = 100

#: ``mod_regression.models.InputType`` — ``RegressionTestCreateSchema.input_type``.
INPUT_TYPES = ('file', 'stdin', 'udp')

#: ``mod_regression.models.OutputType``. Note ``multi_program`` serializes as
#: ``multiprogram``: the enum's *value* is what the API validates against.
OUTPUT_TYPES = ('file', 'null', 'tcp', 'cea708', 'multiprogram', 'stdout', 'report')

#: ``mod_auth.models.Role`` — accepted by ``PATCH /users/{id}``.
USER_ROLES = ('admin', 'contributor', 'tester', 'user')

#: ``RegressionTestCreateSchema.command`` — ``Length(min=1, max=500)``.
COMMAND_MAX_LENGTH = 500

#: Category column widths, shared by the regression-test and category schemas.
NAME_MAX_LENGTH = 64
DESCRIPTION_MAX_LENGTH = 1024

#: ``expected_rc`` is a process exit code — ``Range(min=0, max=255)``.
EXPECTED_RC_MIN = 0
EXPECTED_RC_MAX = 255

#: ``POST /runs`` — ``commit_sha`` must be a full 40-char hex string, no short SHAs.
COMMIT_SHA_LENGTH = 40

#: ``POST /runs`` — ``regression_test_ids`` is capped at ``Length(max=500)``.
MAX_REGRESSION_TEST_IDS = 500

#: ``POST /runs`` — ``branch`` and ``repository`` widths.
BRANCH_MAX_LENGTH = 100
REPOSITORY_MAX_LENGTH = 100

#: ``ForbiddenExtensionCreateSchema`` — alphanumeric, no leading dot, stored lower-cased.
EXTENSION_MAX_LENGTH = 32

#: ``BlockedUserCreateSchema.comment`` — ``Length(max=1024)``.
BLOCKED_USER_COMMENT_MAX_LENGTH = 1024
