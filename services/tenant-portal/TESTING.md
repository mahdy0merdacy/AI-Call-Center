# Testing — Tenant Portal (auth service)

How the auth service is tested, which ISO/IEC 25010 quality characteristic each
test covers, and where to find the proof. Every result comes from a real run
(GitLab job logs and saved reports); nothing is simulated.

## Traceability table

| # | Requirement | Test | Tool | ISO 25010 characteristic | How to see the result |
|---|---|---|---|---|---|
| a | Passwords are stored hashed (Argon2) and only the right password verifies | `src/accounts/tests/test_unit_passwords_tokens.py::test_password_is_hashed_with_argon2_and_verifies` | pytest | Security (confidentiality, integrity), Functional correctness | `pytest` job → Tests tab, `reports/report.xml` |
| a | A valid JWT is accepted; an expired or tampered JWT is rejected | `test_unit_passwords_tokens.py::test_valid_access_token_is_accepted`, `::test_expired_access_token_is_rejected`, `::test_tampered_access_token_is_rejected` | pytest | Security (integrity, authenticity) | `pytest` job → Tests tab, `reports/report.xml` |
| b | A user can register, verify the email, log in and call a protected endpoint (200) | `src/accounts/tests/test_api_auth_flow.py::test_register_login_and_call_protected_endpoint` | pytest + DRF test client + PostgreSQL | Functional suitability (completeness, correctness) | `pytest` job → Tests tab, `reports/report.xml` |
| b | A wrong password is refused with 401 and a generic message | `test_api_auth_flow.py::test_wrong_password_returns_401` | pytest + PostgreSQL | Security (authenticity), Functional correctness | `pytest` job → Tests tab |
| b | A protected endpoint without a token returns 401 | `test_api_auth_flow.py::test_protected_endpoint_without_token_returns_401` | pytest | Security (authenticity) | `pytest` job → Tests tab |
| c | A tenant_admin gets 403 on platform-admin endpoints (but 200 on its own) | `src/accounts/tests/test_roles.py::test_tenant_admin_gets_403_on_platform_admin_endpoint` | pytest + PostgreSQL | Security (confidentiality, integrity) | `pytest` job → Tests tab |
| d | The code has no medium/high-severity security weaknesses | Static analysis of `src/` (tests and migrations excluded) | Bandit | Security | `bandit` job log, `reports/bandit-report.json`, `reports/bandit-junit.xml` |
| — | The code has no syntax errors, undefined names or unused imports | Lint of `src/` (`src/ruff.toml`) | ruff | Maintainability (analysability, modifiability) | `ruff` job log, `reports/ruff-junit.xml` |
| — | Share of application code exercised by the tests | Coverage of `accounts`, `audit`, `config` (`src/.coveragerc`) | pytest-cov | Maintainability (testability) | Coverage % on the `pytest` job, `reports/coverage.xml` |

Not covered yet: **Reliability** (e.g. `/health/` readiness, suspended tenant
blocked immediately), **Performance efficiency**, **Compatibility**. To be added later.

## CI pipeline

- Root `.gitlab-ci.yml` (parent): job `tenant-portal` starts the child pipeline
  `services/tenant-portal/.gitlab-ci.yml` (`trigger: include` + `strategy: depend`)
  only when files under `services/tenant-portal/` (or the root `.gitlab-ci.yml`) change.
- Child pipeline stages: **lint** (`ruff`) → **test** (`pytest`) → **security** (`bandit`).
- `pytest` uses a throwaway PostgreSQL 16 service (`pgvector/pgvector:pg16`, alias `db`),
  the same database engine as production. Test settings (`config/settings/test.py`)
  generate their own RSA keys and secrets for every run.

### Where to find the results in GitLab
1. **Build → Pipelines** → the pipeline → the downstream `tenant-portal` pipeline.
2. **Tests** tab of that child pipeline: each test case with its status (JUnit reports).
3. **Coverage**: percentage shown on the `pytest` job (read from the `TOTAL` line).
4. **Artifacts**: on each job page, *Browse* / *Download* → `services/tenant-portal/reports/`
   (`report.xml`, `coverage.xml`, `bandit-report.json`, `bandit-junit.xml`,
   `ruff-junit.xml`). Kept 30 days, saved even when a job fails.
5. The full job log is the raw proof of each run.

### Required CI/CD variable
| Variable | Value | Settings |
|---|---|---|
| `TEST_DB_PASSWORD` | letters and digits, 16+ characters | Masked, **not** Protected |

No other secret is needed: JWT keys, `DJANGO_SECRET_KEY` and `MFA_ENCRYPTION_KEY`
are generated per run by the test settings. No secret is stored in the repository.

## Run the tests locally

From `services/tenant-portal/`:

```bash
docker compose build portal
docker compose up -d db

# Tests a, b, c with coverage
docker compose run --rm -e RUN_MIGRATIONS=false portal pytest -v --cov --cov-report=term

# Test d: Bandit
docker compose run --rm --no-deps -e RUN_MIGRATIONS=false portal bandit -r . -x ./accounts/tests,./audit/tests,./accounts/migrations,./audit/migrations --severity-level medium

# Lint
docker compose run --rm --no-deps -e RUN_MIGRATIONS=false portal ruff check .
```

## Latest local results (2026-10-23, Python 3.12.14, PostgreSQL 16)

| Check | Result |
|---|---|
| pytest | 8 passed, coverage 69 % (1213 statements, 374 missed) |
| Bandit | No issues identified (0 medium, 0 high; 8 low, all false positives) |
| ruff | All checks passed |
