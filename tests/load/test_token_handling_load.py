#!/usr/bin/env python3
"""PER-86 token-handling load test (issue 061b21b4).

Exercises the two concurrency-exposed surfaces of the wake-time token-handling
design under volume, and its atomic-write failure modes, so the security team has
empirical evidence before cryptographic sign-off.

Surfaces under test:
  A. Concurrent boardroom `.env` credential write — the atomic pattern in
     scripts/wake-maintenance's persist_env_var helper (mktemp -> awk-filter ->
     append key -> chmod 600 -> mv -f). Property: a concurrent reader (the worker's
     load_dotenv) must NEVER observe a partial/corrupt/zero-length file, must
     always see exactly one PAPERCLIP_API_KEY + one ELEVENLABS_API_KEY, a value
     from the valid injected set, and mode 600.
  B. Pending-posts durable queue — the real queue_comment / drain_pending_comments
     in src/papervoice/vendors/paperclip.py.
       B1 concurrent enqueue integrity (os.replace atomicity, no partial JSON).
       B2 concurrent-drain exactly-once — detects the double-post race where two
          wakes drain the same queue and post a queued comment twice.
       D drain crash durability (PER-259) — a drainer SIGKILLed mid-claim (in
          the window between the atomic claim and the unlink/requeue) orphans a
          `*.draining-<pid>` file; a later wake must reclaim it so ZERO queued
          comments are lost. Duplicates on crash are the accepted trade.
  C. Failure mode — a writer crash (SIGKILL) mid token-refresh must leave `.env`
     as either the whole old file or the whole new file, never truncated.

Runs entirely against isolated temp dirs (PAPERCLIP_BOARDROOM_ENV_FILE /
PAPERCLIP_PENDING_POSTS_DIR); it never touches the live boardroom `.env`, the
live queue, or the Paperclip API (post_comment is stubbed with a counter).

Usage:
  PYTHONPATH=src .venv/bin/python tests/load/test_token_handling_load.py
Exit 0 = all scenarios PASS. Exit 1 = a real load defect was observed.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# --- faithful replica of scripts/wake-maintenance:21-29 (atomic token write) ---
# Kept in shell so we test the EXACT mechanics the production wake path uses.
TOKEN_WRITE_SNIPPET = r"""
set -euo pipefail
env_file="$1"; pk="$2"; ek="$3"
mkdir -p "$(dirname "$env_file")"
tmp_file="$(mktemp "${env_file}.tmp.XXXXXX")"
trap 'rm -f "$tmp_file"' EXIT
if [[ -f "$env_file" ]]; then
  awk '!/^PAPERCLIP_API_KEY=/ && !/^ELEVENLABS_API_KEY=/' "$env_file" >"$tmp_file"
fi
printf 'PAPERCLIP_API_KEY=%s\n' "$pk" >>"$tmp_file"
printf 'ELEVENLABS_API_KEY=%s\n' "$ek" >>"$tmp_file"
chmod 600 "$tmp_file"
mv -f "$tmp_file" "$env_file"
"""


def _token_write(env_file: str, pk: str, ek: str) -> None:
    subprocess.run(["bash", "-c", TOKEN_WRITE_SNIPPET, "wake", env_file, pk, ek], check=True)


def _parse_env(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out.setdefault(k, []).append(v)
    return out


# ---------------------------------------------------------------------------
# Scenario A: concurrent token .env refresh — reader never sees corruption
# ---------------------------------------------------------------------------
def _writer_loop(env_file: str, idx: int, rounds: int, base_pk: str, base_ek: str) -> None:
    for r in range(rounds):
        _token_write(env_file, f"{base_pk}-w{idx}-r{r}", f"{base_ek}-w{idx}-r{r}")


def scenario_a(workers: int = 50, rounds: int = 6) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="per86-A-"))
    env_file = tmp / ".env"
    # seed with an unrelated line to prove the filter keeps non-token content.
    # In production the .env is always left at mode 600 by wake-maintenance, so
    # seed it 600 too — any 664 the reader later sees is then a real defect.
    env_file.write_text("OTHER_SETTING=keepme\nPAPERCLIP_API_KEY=seed\nELEVENLABS_API_KEY=seed\n")
    os.chmod(env_file, 0o600)
    valid_pk = {"seed"} | {f"pk-w{i}-r{r}" for i in range(workers) for r in range(rounds)}
    valid_ek = {"seed"} | {f"ek-w{i}-r{r}" for i in range(workers) for r in range(rounds)}

    stop = mp.Event()
    result = mp.Manager().dict(reads=0, corrupt=0, first_bad="")

    def reader():
        reads = corrupt = 0
        first_bad = ""
        while not stop.is_set():
            try:
                st = os.stat(env_file)
                text = env_file.read_text()
            except (FileNotFoundError, OSError):
                # mv -f is atomic; a rename should never expose a missing file
                corrupt += 1
                if not first_bad:
                    first_bad = "file vanished mid-read (rename not atomic?)"
                continue
            reads += 1
            env = _parse_env(text)
            bad = None
            if len(env.get("PAPERCLIP_API_KEY", [])) != 1:
                bad = f"PAPERCLIP_API_KEY count={len(env.get('PAPERCLIP_API_KEY', []))}"
            elif len(env.get("ELEVENLABS_API_KEY", [])) != 1:
                bad = f"ELEVENLABS_API_KEY count={len(env.get('ELEVENLABS_API_KEY', []))}"
            elif env["PAPERCLIP_API_KEY"][0] not in valid_pk:
                bad = f"unknown PK value {env['PAPERCLIP_API_KEY'][0]!r}"
            elif env["ELEVENLABS_API_KEY"][0] not in valid_ek:
                bad = f"unknown EK value {env['ELEVENLABS_API_KEY'][0]!r}"
            elif "OTHER_SETTING" not in env:
                bad = "non-token content lost"
            elif stat.S_IMODE(st.st_mode) != 0o600:
                bad = f"mode={oct(stat.S_IMODE(st.st_mode))}"
            if bad:
                corrupt += 1
                if not first_bad:
                    first_bad = bad
        result["reads"] = reads
        result["corrupt"] = corrupt
        result["first_bad"] = first_bad

    rp = mp.Process(target=reader)
    rp.start()
    procs = [mp.Process(target=_writer_loop, args=(str(env_file), i, rounds, "pk", "ek"))
             for i in range(workers)]
    t0 = time.time()
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    elapsed = time.time() - t0
    stop.set()
    rp.join()

    leftover_tmp = list(tmp.glob(".env.tmp.*"))
    shutil.rmtree(tmp, ignore_errors=True)
    total_writes = workers * rounds
    ok = result["corrupt"] == 0 and not leftover_tmp
    return {
        "name": "A: concurrent .env token refresh",
        "pass": ok,
        "writers": workers, "writes": total_writes, "elapsed_s": round(elapsed, 2),
        "writes_per_s": round(total_writes / elapsed, 1) if elapsed else 0,
        "reader_reads": result["reads"], "corrupt_reads": result["corrupt"],
        "first_bad": result["first_bad"], "leftover_tmp_files": len(leftover_tmp),
    }


# ---------------------------------------------------------------------------
# Scenario B: pending-posts queue — enqueue integrity + drain exactly-once
# ---------------------------------------------------------------------------
def _load_paperclip(pending_dir: str):
    os.environ["PAPERCLIP_PENDING_POSTS_DIR"] = pending_dir
    import importlib
    import papervoice.vendors.paperclip as pc
    importlib.reload(pc)
    return pc


def _enqueue_worker(pending_dir: str, n: int, tag: str) -> None:
    pc = _load_paperclip(pending_dir)
    for i in range(n):
        pc.queue_comment(f"issue-{tag}-{i}", f"body {tag} {i}")


def scenario_b1(procs: int = 8, per_proc: int = 40) -> dict:
    pending = Path(tempfile.mkdtemp(prefix="per86-B1-")) / "pending-posts"
    workers = [mp.Process(target=_enqueue_worker, args=(str(pending), per_proc, f"p{i}"))
               for i in range(procs)]
    for p in workers:
        p.start()
    for p in workers:
        p.join()
    files = list(pending.glob("*.json"))
    partials = list(pending.glob(".pending-*.tmp"))
    bad = 0
    seen = set()
    for f in files:
        try:
            d = json.loads(f.read_text())
            seen.add((d["issue_id"], d["body"]))
        except Exception:
            bad += 1
    expected = procs * per_proc
    shutil.rmtree(pending.parent, ignore_errors=True)
    ok = len(files) == expected and bad == 0 and len(seen) == expected and not partials
    return {
        "name": "B1: concurrent enqueue integrity",
        "pass": ok, "expected": expected, "json_files": len(files),
        "unique_payloads": len(seen), "unparseable": bad, "leftover_tmp": len(partials),
    }


def _drain_worker(pending_dir: str, counter, lock, post_latency_s: float) -> None:
    pc = _load_paperclip(pending_dir)

    def counting_post(issue_id, body):
        # Model the real Paperclip HTTP round-trip. The production post_comment
        # does a network call here; that latency sits BETWEEN read_text and
        # unlink in drain_pending_comments, which is exactly the double-post
        # window. An instant stub hides the race; this exposes it honestly.
        time.sleep(post_latency_s)
        with lock:
            counter.value += 1
        return {"id": "x"}

    pc.post_comment = counting_post
    pc.drain_pending_comments()


def scenario_b2(queued: int = 200, drainers: int = 8, post_latency_s: float = 0.02) -> dict:
    root = Path(tempfile.mkdtemp(prefix="per86-B2-"))
    pending = root / "pending-posts"
    pc = _load_paperclip(str(pending))
    for i in range(queued):
        pc.queue_comment(f"issue-{i}", f"body {i}")
    assert len(list(pending.glob("*.json"))) == queued

    counter = mp.Value("i", 0)
    lock = mp.Lock()
    procs = [mp.Process(target=_drain_worker, args=(str(pending), counter, lock, post_latency_s))
             for _ in range(drainers)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()

    remaining = len(list(pending.glob("*.json")))
    posts = counter.value
    shutil.rmtree(root, ignore_errors=True)
    # exactly-once == every queued item posted exactly once and none left behind
    ok = posts == queued and remaining == 0
    return {
        "name": "B2: concurrent drain exactly-once",
        "pass": ok, "queued": queued, "drainers": drainers,
        "post_latency_ms": int(post_latency_s * 1000),
        "total_posts": posts, "double_posts": max(0, posts - queued),
        "left_in_queue": remaining,
        "note": ("clean" if ok else
                 "DOUBLE-POST RACE: concurrent drains posted a queued comment >1x "
                 "(drain_pending_comments has no cross-process lock / claim step)"),
    }


# ---------------------------------------------------------------------------
# Scenario D: drainer SIGKILLed mid-claim loses zero queued comments (PER-259)
# ---------------------------------------------------------------------------
def _killable_drain_worker(pending_dir: str, posted, lock, latency_s: float) -> None:
    pc = _load_paperclip(pending_dir)

    def counting_post(issue_id, body):
        # Latency keeps the process inside the claim->unlink window so a SIGKILL
        # lands while it holds a *.draining-<pid> claim — exactly the orphan case.
        time.sleep(latency_s)
        with lock:
            posted.append(f"{issue_id}|{body}")
        return {"id": "x"}

    pc.post_comment = counting_post
    pc.drain_pending_comments()


def scenario_d(queued: int = 200, drainers: int = 8,
               kill_after_s: float = 0.05, post_latency_s: float = 0.02) -> dict:
    root = Path(tempfile.mkdtemp(prefix="per259-D-"))
    pending = root / "pending-posts"
    pc = _load_paperclip(str(pending))
    expected = set()
    for i in range(queued):
        pc.queue_comment(f"issue-{i}", f"body {i}")
        expected.add(f"issue-{i}|body {i}")

    mgr = mp.Manager()
    posted = mgr.list()
    lock = mgr.Lock()
    procs = [mp.Process(target=_killable_drain_worker,
                        args=(str(pending), posted, lock, post_latency_s))
             for _ in range(drainers)]
    for p in procs:
        p.start()
    time.sleep(kill_after_s)
    # hard-kill every drainer mid-drain: some die holding a *.draining-<pid> claim
    for p in procs:
        if p.is_alive():
            os.kill(p.pid, signal.SIGKILL)
    for p in procs:
        p.join()

    orphaned_at_kill = len(list(pending.glob("*.draining-*")))

    # a fresh wake: reclaim orphans + drain the rest to completion
    pc2 = _load_paperclip(str(pending))

    def final_post(issue_id, body):
        with lock:
            posted.append(f"{issue_id}|{body}")
        return {"id": "x"}

    pc2.post_comment = final_post
    pc2.drain_pending_comments()

    posted_set = set(posted)
    lost = expected - posted_set
    dupes = len(posted) - len(posted_set)
    remaining = len(list(pending.glob("*.json")))
    orphans_left = len(list(pending.glob("*.draining-*")))
    shutil.rmtree(root, ignore_errors=True)
    # durability == every queued comment posted at least once, queue fully drained
    ok = not lost and remaining == 0 and orphans_left == 0
    return {
        "name": "D: SIGKILL mid-claim loses zero comments",
        "pass": ok, "queued": queued, "drainers": drainers,
        "post_latency_ms": int(post_latency_s * 1000),
        "orphaned_claims_at_kill": orphaned_at_kill,
        "posted_unique": len(posted_set), "lost": len(lost),
        "duplicates_on_crash": dupes,
        "left_in_queue": remaining, "orphans_left": orphans_left,
        "note": ("clean — every queued comment survived a mid-claim crash"
                 if ok else
                 f"LOST {len(lost)} comment(s): a mid-claim crash orphaned a "
                 "*.draining-<pid> file that no later wake reclaimed"),
    }


# ---------------------------------------------------------------------------
# Scenario C: crash mid-write leaves .env whole (never truncated)
# ---------------------------------------------------------------------------
def scenario_c(trials: int = 40) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="per86-C-"))
    env_file = tmp / ".env"
    good = "OTHER=x\nPAPERCLIP_API_KEY=OLD\nELEVENLABS_API_KEY=OLD\n"
    env_file.write_text(good)
    truncated = 0
    first_bad = ""
    # A writer that fsyncs a temp file then sleeps just before mv, so a SIGKILL
    # lands in the danger window. Atomic rename => reader sees OLD or NEW, never half.
    snippet = r"""
set -euo pipefail
env_file="$1"; pk="$2"
tmp_file="$(mktemp "${env_file}.tmp.XXXXXX")"
awk '!/^PAPERCLIP_API_KEY=/ && !/^ELEVENLABS_API_KEY=/' "$env_file" >"$tmp_file"
printf 'PAPERCLIP_API_KEY=%s\n' "$pk" >>"$tmp_file"
printf 'ELEVENLABS_API_KEY=%s\n' "$pk" >>"$tmp_file"
chmod 600 "$tmp_file"
sync
sleep 0.5
mv -f "$tmp_file" "$env_file"
"""
    for t in range(trials):
        proc = subprocess.Popen(["bash", "-c", snippet, "w", str(env_file), f"NEW{t}"])
        time.sleep(0.05)  # let it reach the pre-mv window
        proc.send_signal(signal.SIGKILL)
        proc.wait()
        text = env_file.read_text()
        env = _parse_env(text)
        pk = env.get("PAPERCLIP_API_KEY", [])
        ek = env.get("ELEVENLABS_API_KEY", [])
        if len(pk) != 1 or len(ek) != 1 or "OTHER" not in env:
            truncated += 1
            if not first_bad:
                first_bad = repr(text[:120])
    for stray in tmp.glob(".env.tmp.*"):
        stray.unlink()
    shutil.rmtree(tmp, ignore_errors=True)
    return {
        "name": "C: SIGKILL mid-write leaves .env whole",
        "pass": truncated == 0, "trials": trials,
        "truncated_or_corrupt": truncated, "first_bad": first_bad,
    }


def main() -> int:
    mp.set_start_method("fork", force=True)
    print("PER-86 token-handling load test (issue 061b21b4)\n" + "=" * 60)
    results = [scenario_a(), scenario_b1(), scenario_b2(), scenario_d(), scenario_c()]
    print()
    all_ok = True
    for r in results:
        status = "PASS" if r["pass"] else "FAIL"
        all_ok = all_ok and r["pass"]
        print(f"[{status}] {r['name']}")
        for k, v in r.items():
            if k in ("name", "pass"):
                continue
            print(f"        {k}: {v}")
    print("=" * 60)
    print("OVERALL:", "PASS" if all_ok else "FAIL")
    print("\nJSON:", json.dumps(results))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
