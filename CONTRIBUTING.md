# Contributing

How we work on AI Call Center. Read this before opening your first merge request.
For running the stack locally, see [RUNNING.md](RUNNING.md).

## TL;DR

1. Pick or create an issue, assign yourself, move it to **In progress**.
2. Branch off `main`: `feat/12-stt-streaming`.
3. Commit with Conventional Commits: `feat(stt): stream partial transcripts`.
4. Open a merge request with `Closes #12`, fill in the template.
5. Pipeline green + 1 review + threads resolved → squash & merge.

---

## Repository and access

- **GitLab is the source of truth.** Push only to GitLab. The GitHub repo is an automatic read-only mirror. Never push or open PRs there.
- Everyone on the team has the **Developer** role: you can push branches and open, review and merge MRs. Settings are Maintainer-only.
- `main` is protected. Nobody pushes to it directly, and nobody force-pushes it.
- Set your commit email to the one on your GitLab account so your work is credited to you:
  ```bash
  git config user.email you@example.com
  ```
- Never commit secrets. Local config goes in `.env` / `data/.env` (gitignored). Commit a `.env.example` with placeholder values instead.

## Branching

We use GitHub Flow: `main` is always deployable, and all work happens on short-lived branches merged back through MRs.

Branch names: `<type>/<issue#>-<short-description>`

```
feat/12-stt-streaming
fix/31-orchestrator-timeout
chore/40-ci-pip-cache
```

- One issue per branch and per MR.
- Keep branches short-lived (days, not weeks). Rebase on `main` often:
  ```bash
  git fetch origin && git rebase origin/main
  ```
- Experiments belong in `spikes/`, not half-merged into a service.

## Commit messages

We use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <summary in imperative mood, lowercase, no period>
```

| Type | Use for |
|---|---|
| `feat` | New behaviour |
| `fix` | Bug fix |
| `refactor` | Code change with no behaviour change |
| `test` | Adding or fixing tests |
| `docs` | Documentation only |
| `chore` | Tooling, dependencies, housekeeping |
| `ci` | Pipeline changes |

The scope is the area you touched: `media-edge`, `orchestrator`, `stt`, `mcp-servers`, `data`, `proto`, `ci`.

```
feat(stt): stream partial transcripts over gRPC
fix(media-edge): handle ICE restart on reconnect
chore(ci): cache pip downloads
```

MRs are squashed on merge, so the **MR title** becomes the final commit message. It must follow this format too.

## Merge requests

- Link the issue: `Closes #12` in the description.
- Aim for **under ~400 changed lines**. Split anything bigger.
- Fill in the MR template: *What changed*, *Why*, *How to test*, *Checklist*.
- Mark it **Draft** until it is ready for review.

An MR can be merged when:

- [ ] The pipeline is green (enforced)
- [ ] All review threads are resolved (enforced)
- [ ] At least **1 approval from someone other than the author** (team rule)
- [ ] The branch is rebased on the latest `main` (fast-forward merges only)

Merge with **squash**, and delete the source branch.

### Reviewing

- Review within one working day, or say when you will.
- Comment on the code, not the person. Prefix optional suggestions with `nit:`.
- Approve only what you have actually read and understood. If it touches an area you don't know, say so and pull in someone from `CODEOWNERS`.

## Code standards

These are checked in CI. Run them locally first, ideally through the pre-commit hooks.

| Area | Tool | Command |
|---|---|---|
| Python lint | ruff | `ruff check .` |
| Python format | ruff | `ruff format --check .` |
| Types | mypy | `mypy <service>` |
| Tests | pytest | `pytest <service>` |
| Protobuf | buf | `buf lint` |
| Dockerfiles | hadolint | `hadolint <path>/Dockerfile` |
| Secrets | GitLab Secret Detection | runs in CI |

Setting up the hooks once per clone:

```bash
pip install pre-commit
pre-commit install
```

### Rules

- **Tests:** a bug fix comes with a test that fails without the fix. New behaviour comes with tests.
- **Generated code:** files under `gen/` are generated from `.proto` files. Never edit them by hand. Change the `.proto`, regenerate, and commit both together. CI fails if `gen/` is out of date.
- **Dependencies:** pin versions in each service's `requirements.txt`. Mention new dependencies in the MR description.
- **Config:** read config from environment variables, never hard-code hosts, ports or credentials.
- **Docs:** if you change how something is run, update `RUNNING.md` or the service README in the same MR.

## Tracking work

- **Every piece of work has an issue.** If it isn't on the board, it isn't happening.
- **Board columns:** `To do → In progress → In review → Done`. Keep your own cards up to date.
- **Labels:**
  - type: `bug`, `feature`, `infra`, `docs`
  - area: `media-edge`, `orchestrator`, `stt`, `mcp-servers`, `data`
- **Milestones:** 1–2 week iterations, with a short demo or review at the end of each.
- Blocked? Say so on the issue and in the team chat. Don't wait silently.

### Definition of Done

An issue is done when its MR is:

- merged into `main`, with a green pipeline,
- reviewed and approved,
- covered by tests where it makes sense,
- reflected in the docs if setup or behaviour changed,
- deployed, once CD is in place.

---

## Maintainer setup checklist

One-time GitLab configuration. Only Maintainers or Owners can do these.

**Members** (group `student-group213543`, Manage → Members)
- [ ] Invite teammates at the **group** level with the **Developer** role
- [ ] Keep Owner/Maintainer to one or two people

**Protected branches** (Settings → Repository → Protected branches, `main`)
- [ ] Allowed to merge: Developers + Maintainers
- [ ] Allowed to push and merge: No one
- [ ] Allowed to force push: off

**Merge requests** (Settings → Merge requests)
- [ ] Merge method: Fast-forward merge
- [ ] Squash commits when merging: Encourage
- [ ] Pipelines must succeed
- [ ] All threads must be resolved
- [ ] Enable "Delete source branch" by default

**Repository files to add**
- [ ] `.gitlab/merge_request_templates/Default.md`
- [ ] `.gitlab/issue_templates/Bug.md` and `Feature.md`
- [ ] `CODEOWNERS` (suggests reviewers; enforcement is a paid feature)
- [ ] `.pre-commit-config.yaml`, ruff config in `pyproject.toml`, `.editorconfig`
- [ ] `.gitlab-ci.yml`: lint, test and secret detection on MRs, then image builds to the Container Registry on `main`

**Plan**
- [ ] Create the labels listed above
- [ ] Create an issue board with the four columns
- [ ] Create the first milestone

> GitLab Free cannot enforce required approvals, CODEOWNERS approval, or commit-message rules. The pipeline and the team rules above cover those gaps.
