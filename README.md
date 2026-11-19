# GitHub Actions runner benchmarks

Twelve open-source projects' own CI jobs, each pinned to a release commit, run on GitHub's standard
hosted runner and on Sengi runners (Basic and Premium, 2 and 4 vCPU). Every run is a public GitHub
Actions run in this repository, so every number can be opened and checked. The results are in
[`results/results.json`](results/results.json) and [`results/results.csv`](results/results.csv),
and on [sengi.run/benchmarks](https://sengi.run/benchmarks).

## What runs

| Project | Commit (release) | Job |
|---|---|---|
| psf/black | `87928e6d` (26.5.1) | Python 3.13 test suite (tox) |
| cli/cli | `fc4b137c` (v2.102.0) | Unit and integration tests with the race detector, then a build |
| openai/codex | `01fc69f4` (rust-v0.159.3) | `cargo clippy` over the Rust workspace, tests included |
| mastodon/mastodon | `3987b9fd` (v4.7.2) | Docker image build after a one-file change, layer cache on |
| pallets/flask | `22d92470` (3.1.3) | Python 3.13 test suite (tox) |
| google/guava | `666338cc` (v33.7.2) | Maven install and verify on JDK 25 |
| vercel/ai | `45e1fbc3` (ai@7.0.126) | Turborepo build of three Next.js examples |
| pydantic/pydantic | `001dea02` (v2.13.5) | pytest suite on Python 3.13 |
| microsoft/TypeScript | `050880ce` (v6.0.3) | Compiler test suite on Node 24 |
| vitejs/vite | `39ddf7cc` (v8.3.1) | Build, unit tests and Playwright end-to-end tests on Node 24 |
| vuejs/core | `5be58b4c` (v3.5.43) | Unit tests (Vitest) |
| sharkdp/fd | `4f817787` (v10.5.0) | `cargo build --release`, then `cargo test` |

Full commit SHAs are in [`config/projects.json`](config/projects.json) and in each job.

**How the list was chosen.** The project list is the whole workload list of an existing
open-source CI benchmark suite (Apache-2.0; see [NOTICE](NOTICE)). We adopted the whole list before
running it here and did not pick the projects. Every project that ran is in the results, including
the ones where Sengi is slower. A project would be left out only if its build also fails on GitHub's
runner, and it would be listed with the reason. The projects are named only to identify the code
that was built; none of them is affiliated with this benchmark.

Each job is the project's own CI job. Changes from the project's job are marked `PORTABILITY` in
[`.github/workflows/bench.yml`](.github/workflows/bench.yml) and are the same on every runner (for
example, a stock Ubuntu image needs a few system packages that GitHub's image already has).

## Method

- **Runners.** `ubuntu-24.04` (GitHub's standard runner; on a public repository it has 4 vCPU),
  `sengi-basic-2`, `sengi-premium-2`, `sengi-basic-4` and `sengi-premium-4`, all on Ubuntu 24.04
  (GitHub's `ubuntu-latest` has moved to a newer Ubuntu, so it is not used). Every
  job records the vCPU count, CPU and memory it ran on (the `rb-probe` step); the results print the
  measured vCPU count, not a nominal one.
- **Customer priority.** Sengi jobs must run at the priority a paying customer gets. Sengi's runner
  setup tells each job its priority (`SENGI_RUNNER_PRIORITY`, `customer` or `internal`); this
  repository's GitHub account is Sengi's own, whose jobs run at internal priority unless Sengi lists
  the repository to run as a customer. The `rb-probe` step records the priority and fails any Sengi
  job that is not at `customer`, and jobs recorded before this check (2026-11-19) are not measured.
- **One run = one runner class x all twelve projects.** The jobs run in four lanes, so at most four
  run at once. Runner classes are run one at a time, never together.
- **Warm-up.** Each runner class first does one warm-up run that fills its dependency caches, as a
  project's CI usually has them. Warm-up runs are listed in the results and not measured.
- **Repetitions.** Five measured runs per project per runner. The results give the median with n,
  min and max. A median is shown only when n is at least 3.
- **Time.** Job run time: from the job's start to its end, read from the GitHub API. Time waiting
  in the queue is recorded separately and is not in the headline time.
- **Cost.** Billed minutes (each job's time rounded up to a whole minute, as both GitHub and Sengi
  bill) times the runner's list price per minute, read from
  <https://sengi.run/data/cheapest-github-actions-runners.json>. GitHub does not bill public
  repositories for its standard runner; the GitHub cost shown is what a private repository pays for
  the same minutes at GitHub's listed rate for the measured size.
- **Exclusions.** Every job is in `results/results.csv`. A job is left out of the medians only for
  the reason recorded next to it in `results/results.json` (`excluded`): a warm-up or pilot run, a
  failed, timed-out or cancelled job, or a machine of a different size from the rest of its class.
  Failed jobs are also counted in each row's `failures`.
- **Schedule.** One run per runner class every Sunday (UTC), two hours apart, then the collector.

## Reproduce

Fork this repository, then run the bench workflow for a runner class and repetition label, one
class at a time:

```sh
gh workflow run bench.yml -f runner=ubuntu-24.04 -f rep=warmup
gh workflow run bench.yml -f runner=ubuntu-24.04 -f rep=1
```

Then collect (needs the `gh` CLI, Python 3 and read access to the repository's Actions):

```sh
GITHUB_REPOSITORY=<you>/benchmarks python3 scripts/collect.py
```

`hw.yml` records one runner's hardware and a short CPU benchmark (sysbench); it is not part of the
timings.

## Safety of this repository

The workflows start only by hand (`workflow_dispatch`) or on a schedule; pull requests from forks
never start a job. Measuring jobs have read-only access to this repository, and no workflow reads a
secret. Every action is pinned to a full commit SHA. `scripts/check_workflows.rb` checks all of this
and runs before every collection.

## Licence

Apache License 2.0; see [LICENSE](LICENSE) and [NOTICE](NOTICE).
