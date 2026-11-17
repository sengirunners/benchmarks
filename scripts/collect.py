#!/usr/bin/env python3
"""Collect the bench runs into results/results.json and results/results.csv.

    GH_TOKEN=... python3 scripts/collect.py

Reads every completed run of .github/workflows/bench.yml through the gh CLI. Each job is stored once,
by job id, in results/jobs.json (append-only; a run already stored is not read again). From that
store it writes:

- results/results.json: the method, the runners (with the vCPU count each job reported), the projects
  (with their pinned commits), one summary row per project and runner (median, min, max, n), every
  excluded run or job with its reason, and every measured job with its link.
- results/results.csv: one row per job, the excluded ones included, with the reason.

A job counts only if it succeeded, is not from a warm-up or pilot run, ran on a machine of its class's
size, and is not excluded by hand in config/exclusions.json. Failed and timed-out jobs are listed and
counted as failures. Time is the job's run time (start to end); queue time is kept apart.
"""
import csv
import json
import math
import os
import pathlib
import re
import statistics
import subprocess
import sys
import urllib.request
from collections import Counter
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
REPO = os.environ.get("GITHUB_REPOSITORY", "sengirunners/benchmarks")
WORKFLOW = "bench.yml"
RESULTS = ROOT / "results"
STORE = RESULTS / "jobs.json"
NOT_MEASURED_REPS = {"warmup": "warm-up run: fills each runner's dependency caches first; not measured",
                     "pilot": "pilot run: checks the workflows; not measured"}
PROBE = re.compile(r'rb-probe cpu="([^"]*)" ghz=(\S+) nproc=(\d+) mem_kib=(\d+)')
MIN_RUNS = 3


def gh_json(path):
    out = subprocess.run(["gh", "api", "-H", "Accept: application/vnd.github+json", path],
                         check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def gh_pages(path, key):
    items, page = [], 1
    while True:
        sep = "&" if "?" in path else "?"
        data = gh_json(f"{path}{sep}per_page=100&page={page}")
        items += data[key]
        if len(data[key]) < 100:
            return items
        page += 1


def job_log(job_id):
    # Newer gh refuses output with terminal escapes (job logs have colour codes) unless told to allow it.
    for extra in (["--allow-escape-sequences"], []):
        done = subprocess.run(["gh", "api", *extra, f"repos/{REPO}/actions/jobs/{job_id}/logs"],
                              capture_output=True, text=True)
        if done.returncode == 0:
            return done.stdout
    print(f"warning: no log for job {job_id}: {done.stderr.strip()[:200]}", file=sys.stderr)
    return ""


def probe(row):
    if row["conclusion"] in ("success", "failure", "timed_out") and row["started_at"]:
        m = PROBE.search(job_log(row["job_id"]))
        if m:
            row.update(cpu=m.group(1), nproc=int(m.group(3)), mem_kib=int(m.group(4)))


def ts(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def secs(start, end):
    return int((ts(end) - ts(start)).total_seconds()) if start and end else None


def read_run(run):
    rows = []
    for job in gh_pages(f"repos/{REPO}/actions/runs/{run['id']}/jobs", "jobs"):
        parts = [p.strip() for p in job["name"].split(" / ")]
        if len(parts) != 3 or not parts[2].startswith("rep "):
            continue  # the plan job
        project, label, rep = parts[0], parts[1], parts[2].removeprefix("rep ")
        row = {
            "run_id": run["id"], "run_url": run["html_url"], "head_sha": run["head_sha"], "event": run["event"],
            "job_id": job["id"], "job_url": job["html_url"], "project": project, "label": label, "rep": rep,
            "conclusion": job["conclusion"], "created_at": job["created_at"], "started_at": job["started_at"],
            "completed_at": job["completed_at"],
            "seconds": secs(job["started_at"], job["completed_at"]),
            "queue_s": secs(job["created_at"], job["started_at"]),
            "runner_name": job.get("runner_name"),
            "cpu": None, "nproc": None, "mem_kib": None,
        }
        probe(row)
        rows.append(row)
    return rows


def median(values):
    m = statistics.median(values)
    return int(m) if float(m).is_integer() else m


def billed(seconds):
    return max(1, math.ceil(seconds / 60))


def main():
    projects = json.loads((ROOT / "config/projects.json").read_text())["projects"]
    rconf = json.loads((ROOT / "config/runners.json").read_text())
    manual = json.loads((ROOT / "config/exclusions.json").read_text())
    store = json.loads(STORE.read_text()) if STORE.exists() else []
    seen = {row["run_id"] for row in store}

    runs = gh_pages(f"repos/{REPO}/actions/workflows/{WORKFLOW}/runs?status=completed", "workflow_runs")
    for run in sorted(runs, key=lambda r: r["id"]):
        if run["id"] not in seen:
            new = read_run(run)
            print(f"run {run['id']}: {len(new)} jobs")
            store += new
    for row in store:
        if row["nproc"] is None:
            probe(row)  # a log that could not be read last time
    store.sort(key=lambda r: (r["run_id"], r["job_id"]))
    RESULTS.mkdir(exist_ok=True)
    STORE.write_text(json.dumps(store, indent=1) + "\n")

    by_label = {r["label"]: r for r in rconf["runners"]}
    pkeys = [p["key"] for p in projects]
    # The vCPU count a class runs on: what its successful jobs reported, most often.
    vcpu = {}
    for label in by_label:
        counts = Counter(r["nproc"] for r in store if r["label"] == label and r["nproc"])
        if counts:
            vcpu[label] = counts.most_common(1)[0][0]

    excluded_runs, excluded_jobs = {}, {}
    for row in store:
        reason = manual["runs"].get(str(row["run_id"])) or NOT_MEASURED_REPS.get(row["rep"])
        if reason:
            excluded_runs.setdefault(row["run_url"], reason)
            row["excluded"] = reason
            continue
        reason = manual["jobs"].get(str(row["job_id"]))
        if not reason and row["label"] not in by_label:
            reason = f"runner {row['label']} is not in config/runners.json"
        if not reason and row["project"] not in pkeys:
            reason = f"project {row['project']} is not in config/projects.json"
        if not reason and row["conclusion"] in ("failure", "timed_out"):
            reason = f"job {row['conclusion'].replace('_', ' ')}"
        if not reason and row["conclusion"] != "success":
            reason = f"job {row['conclusion'] or 'did not finish'}"
        if not reason and row["nproc"] is None:
            reason = "the job's log has no vCPU record"
        if not reason and row["nproc"] != vcpu.get(row["label"]):
            reason = f"ran on a {row['nproc']} vCPU machine, not {vcpu.get(row['label'])} like the rest of its class"
        if reason:
            row["excluded"] = reason
            excluded_jobs[row["job_url"]] = reason
        else:
            row.pop("excluded", None)

    measured = [r for r in store if "excluded" not in r]
    # Every measured job must come from the same workflow content; the page links one commit.
    shas = Counter(r["head_sha"] for r in measured)
    bench_commit = shas.most_common(1)[0][0] if shas else None
    for sha in shas:
        if sha != bench_commit:
            diff = subprocess.run(["git", "-C", str(ROOT), "diff", "--quiet", sha, bench_commit, "--",
                                   ".github/workflows", "config/projects.json"])
            if diff.returncode != 0:
                sys.exit(f"measured runs at {sha} and {bench_commit} ran different workflows; exclude one set")

    req = urllib.request.Request(rconf["price_source"], headers={"User-Agent": "benchmarks-collector/1 (+https://github.com/sengirunners/benchmarks)"})
    prices = json.loads(urllib.request.urlopen(req, timeout=30).read())
    rows = {r["runner"]: r for r in prices["price_table"]["rows"]}
    runners = []
    for r in rconf["runners"]:
        n = vcpu.get(r["label"])
        if n is None or not any(m["label"] == r["label"] for m in measured):
            continue
        rate = rows[rconf["price_rows"][r["tier"]]]["usd_per_minute"][str(n)]
        runners.append({"key": r["key"], "label": r["label"], "name": f"{r['name']}, {n} vCPU", "tier": r["tier"],
                        "vcpu_measured": n, "usd_per_minute": rate})
    rate = {r["label"]: r["usd_per_minute"] for r in runners}
    key_of = {r["label"]: r["key"] for r in rconf["runners"]}

    summary = []
    for p in projects:
        for r in runners:
            ok = [m for m in measured if m["project"] == p["key"] and m["label"] == r["label"]]
            fails = [m for m in store if m["project"] == p["key"] and m["label"] == r["label"]
                     and m.get("excluded", "").startswith(("job failure", "job timed out"))]
            if not ok and not fails:
                continue
            row = {"project": p["key"], "runner": r["key"], "runs": len(ok), "failures": len(fails),
                   "median_s": None, "min_s": None, "max_s": None, "median_queue_s": None,
                   "median_billed_minutes": None, "median_cost_usd": None}
            if ok:
                bm = median([billed(m["seconds"]) for m in ok])
                row.update(median_s=median([m["seconds"] for m in ok]), min_s=min(m["seconds"] for m in ok),
                           max_s=max(m["seconds"] for m in ok), median_queue_s=median([m["queue_s"] for m in ok]),
                           median_billed_minutes=bm, median_cost_usd=round(bm * rate[r["label"]], 6))
            summary.append(row)

    porder = {k: i for i, k in enumerate(pkeys)}
    rorder = {r["label"]: i for i, r in enumerate(rconf["runners"])}
    # Measured jobs plus the measured-round jobs that did not succeed (failed, timed out, cancelled),
    # each with its conclusion. Warm-up, pilot, hand-excluded and wrong-size jobs are only in `excluded`.
    listed = measured + [m for m in store if m.get("excluded", "").startswith("job ") and m["started_at"]
                         and m["label"] in rorder and m["project"] in porder]
    runs_out = [{"project": m["project"], "runner": key_of[m["label"]], "rep": m["rep"], "conclusion": m["conclusion"],
                 "seconds": m["seconds"], "queue_s": m["queue_s"], "run_url": m["run_url"], "job_url": m["job_url"]}
                for m in sorted(listed, key=lambda m: (porder[m["project"]], rorder[m["label"]], m["run_id"]))]
    days = sorted({m["completed_at"][:10] for m in measured})
    out = {
        "page": "https://sengi.run/benchmarks",
        "measured": days[-1] if days else None,
        "last_checked": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "bench_repo": REPO,
        "bench_commit": bench_commit,
        "method": {
            "time": "Job run time, start to end, from the GitHub API. Queue time is kept apart.",
            "runs": f"Median of the measured runs, with n, min and max; a median needs n >= {MIN_RUNS}.",
            "cost": "Billed minutes (each job rounded up to a whole minute) times the runner's list price per minute.",
            "github_cost": "GitHub does not bill public repositories for this runner; its cost is what a private repository pays at GitHub's listed rate for the measured size.",
            "order": "One runner class at a time; at most four jobs at once within a run. One warm-up run per class first fills dependency caches.",
            "selection": "The project list is the whole workload list of an existing open-source CI benchmark suite, adopted before running it here. Each project is pinned to a release commit.",
            "exclusions": "Every run is listed. A job is left out of the medians only for the reason given next to it.",
            "dates": f"{days[0]} to {days[-1]}" if days else None,
        },
        "runners": runners,
        "projects": [{k: p[k] for k in ("key", "name", "repo", "commit", "language", "what_runs")} for p in projects],
        "summary": summary,
        "excluded": [{"run_url": u, "reason": why} for u, why in list(excluded_runs.items()) + list(excluded_jobs.items())],
        "runs": runs_out,
    }
    (RESULTS / "results.json").write_text(json.dumps(out, indent=1) + "\n")

    fields = ["project", "runner", "label", "rep", "conclusion", "seconds", "queue_s", "billed_minutes", "usd_per_minute",
              "cost_usd", "vcpu", "cpu", "mem_kib", "started_at", "completed_at", "excluded", "run_url", "job_url"]
    with (RESULTS / "results.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for m in store:
            r = rate.get(m["label"])
            bm = billed(m["seconds"]) if m["seconds"] is not None else None
            w.writerow({"project": m["project"], "runner": key_of.get(m["label"], ""), "label": m["label"], "rep": m["rep"],
                        "conclusion": m["conclusion"], "seconds": m["seconds"], "queue_s": m["queue_s"],
                        "billed_minutes": bm, "usd_per_minute": r,
                        "cost_usd": round(bm * r, 6) if bm is not None and r is not None else None,
                        "vcpu": m["nproc"], "cpu": m["cpu"], "mem_kib": m["mem_kib"], "started_at": m["started_at"],
                        "completed_at": m["completed_at"], "excluded": m.get("excluded", ""),
                        "run_url": m["run_url"], "job_url": m["job_url"]})

    for s in summary:
        print(json.dumps(s))
    print(f"{len(measured)} measured jobs, {len(excluded_runs)} excluded runs, {len(excluded_jobs)} excluded jobs")


if __name__ == "__main__":
    main()
