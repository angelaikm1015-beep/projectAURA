"""Where a running generation reports how far it has got.

Writing a score takes minutes, not the "minute or two" the form used to promise.
A browser sitting on a silent request for six minutes cannot tell a slow job
from a broken one, and neither could the person watching it.

The generation stays one long POST — it is simpler than a job queue and the
result comes back in the same response. Progress travels alongside it: the
client makes up an id, sends it with the request, and polls for that id while it
waits. Nothing here is durable, because nothing here needs to outlive the
request it describes.
"""

import math
import threading
import time

# How long a finished entry sticks around. Long enough for the last poll to
# land — and for a deep piece, whose result is collected by a later poll — short
# enough that a long-running server does not accumulate them.
TTL_SECONDS = 60 * 60
# A running entry is only given up on after this long. Deep composing takes the
# better part of an hour for a big ensemble.
RUNNING_TTL_SECONDS = 6 * 60 * 60

# Measured on a 15-part, 40-bar brief with the configured model: planning ~90s,
# one round of part-writing ~120s, the correction round ~90s when it is needed.
# These are what the estimate is built from, and they are deliberately a little
# pessimistic — a job that finishes early is a pleasant surprise, one that
# overruns its own estimate is the problem being fixed here.
PLAN_SECONDS = 95
WRITE_ROUND_SECONDS = 130
CHECK_SECONDS = 5
FIX_SECONDS = 95

# Fraction of the whole each stage accounts for, used for the bar.
# Measured on a real 14-part run: planning took 32% of the wall clock, writing
# 61%, correcting 7%. Correcting is the variable one — some drafts need several
# rounds of batches — so it keeps a little more than it averaged.
STAGE_WEIGHTS = {"planning": 0.28, "writing": 0.55, "checking": 0.02,
                 "fixing": 0.12, "engraving": 0.03}
STAGE_ORDER = ["planning", "writing", "checking", "fixing", "engraving"]

# Roughly how long each stage takes on its own, used only to let the bar creep
# while a single long call is in flight.
STAGE_SECONDS = {"planning": 95, "writing": 130, "checking": 5,
                 "fixing": 95, "engraving": 2}

STAGE_LABELS = {
    "planning": "Planning the piece — its themes, harmony and form",
    "writing": "Writing the parts",
    "checking": "Adding up every bar",
    "fixing": "Correcting what doesn't fit",
    "engraving": "Engraving the sheets",
    "done": "Finished",
    "failed": "Stopped",
}

# Deep composing (deep.py) is one long architecture call, the melody, and then
# the parts one at a time; checking and correcting happen inside each step.
MODES = {
    "light": {"order": STAGE_ORDER, "weights": STAGE_WEIGHTS, "seconds": STAGE_SECONDS,
              "labels": STAGE_LABELS},
    "deep": {
        # Shares of the time, not of the calls: the plan is one long call, and
        # everything after it runs at once, so the two take about as long.
        "order": ["planning", "writing", "engraving"],
        "weights": {"planning": 0.45, "writing": 0.52, "engraving": 0.03},
        "seconds": {"planning": 240, "writing": 240, "engraving": 2},
        "labels": {**STAGE_LABELS,
                   "planning": "Composing the themes, the form and every bar's harmony",
                   "writing": "Writing the full score and the solos — every passage at "
                              "once"},
    },
}

_entries = {}
_lock = threading.Lock()


def estimate_seconds(part_count: int, batch_size: int = 5) -> dict:
    """Roughly how long this will take, as a range.

    A range rather than a number, because the honest answer is a range: the same
    fifteen-part brief took 143s, 240s and 342s on three different models, and
    the correction round only happens when the first draft needs it. A single
    figure would be the same kind of lie as "a minute or two".

    Batches are written concurrently, so part count matters far less than it
    looks: what counts is how many *rounds* of batches there are, and with a cap
    on concurrency that grows slowly.
    """
    batches = max(1, -(-part_count // max(1, batch_size)))
    rounds = max(1, -(-batches // 4))  # MAX_CONCURRENT_BATCHES

    # Planning is one call, but it is a harder call for a big ensemble: it has
    # to give every one of them something to do.
    planning = PLAN_SECONDS * (0.7 + 0.02 * min(part_count, 30))

    best = planning + rounds * WRITE_ROUND_SECONDS + CHECK_SECONDS
    worst = best + FIX_SECONDS          # a draft that needs correcting
    return {"lowSeconds": int(best * 0.75), "highSeconds": int(worst * 1.15),
            "typicalSeconds": int((best + worst) / 2)}


def start(request_id: str, part_count: int, estimate: dict, mode: str = "light",
          owner: str | None = None) -> None:
    if not request_id:
        return
    _sweep()
    with _lock:
        _entries[request_id] = {
            "stage": "planning", "done": 0, "total": 1,
            "partCount": part_count, **estimate, "mode": mode,
            "startedAt": time.time(), "stageStartedAt": time.time(),
            "finishedAt": None, "error": None, "ready": False,
            "_owner": owner, "_result": None,
        }


def report(request_id: str, stage: str, done: int, total: int) -> None:
    """Called from the generation, including from its worker threads."""
    if not request_id:
        return
    with _lock:
        entry = _entries.get(request_id)
        if entry is None:
            return
        # Batches finish out of order; never let the bar jump backwards.
        if entry["stage"] == stage and done < entry["done"]:
            return
        if entry["stage"] != stage:
            entry["stageStartedAt"] = time.time()
        entry.update(stage=stage, done=done, total=max(1, total))


def finish(request_id: str, error: str | None = None, result: dict | None = None) -> None:
    """``result`` is kept for a background generation to be collected later."""
    if not request_id:
        return
    with _lock:
        entry = _entries.get(request_id)
        if entry is not None:
            entry.update(finishedAt=time.time(), error=error,
                         stage="done" if error is None else "failed",
                         ready=result is not None, _result=result)


def result(request_id: str, owner: str) -> dict | None:
    """The finished piece of a background generation — only for whoever asked."""
    with _lock:
        entry = _entries.get(request_id)
        if entry is None or entry["_owner"] != owner:
            return None
        return entry["_result"]


def read(request_id: str) -> dict | None:
    with _lock:
        entry = _entries.get(request_id)
        if entry is None:
            return None
        entry = {key: value for key, value in entry.items() if not key.startswith("_")}

    elapsed = (entry["finishedAt"] or time.time()) - entry["startedAt"]
    entry["elapsedSeconds"] = int(elapsed)
    entry["label"] = MODES[entry["mode"]]["labels"].get(entry["stage"], "Working…")
    # Two readings of the same progress. The bar uses the creeping one so it
    # keeps moving during a long opaque call; the time estimate uses only work
    # that has genuinely finished, because projecting from a deliberately
    # conservative creep produces a "time left" that grows as you watch it.
    fraction = _fraction(entry)
    solid = _fraction(entry, creep=False)
    entry["percent"] = 100 if entry["stage"] == "done" else min(99, int(fraction * 100))

    if entry["stage"] in ("done", "failed"):
        entry["remainingSeconds"] = 0
        entry["overrunning"] = False
        return entry

    # Re-estimate from what this run has actually done rather than from the
    # guess made before it started: if planning took twice as long as expected,
    # so will everything else, and the number on screen should say so.
    if solid > 0.15 and elapsed > 20:
        # Enough has finished to project from how fast this run is going.
        remaining = (elapsed / solid) - elapsed
    elif elapsed < entry["typicalSeconds"] * 0.8:
        remaining = entry["typicalSeconds"] - elapsed
    else:
        # Nothing has completed and we are already near the estimate. We do not
        # know how much longer this will take, and a floor of "10s left" held
        # for six minutes is exactly the lie this panel exists to stop telling.
        remaining = None

    overrunning = (remaining is None) or elapsed > entry["highSeconds"]
    remaining = _steady(request_id, remaining, time.time())
    entry["remainingSeconds"] = int(remaining) if remaining and remaining > 15 else None
    entry["overrunning"] = overrunning
    return entry


# The projection above rises while a long call runs (time passes, nothing new
# has finished) and drops when one lands: shown raw, "time left" goes 4 min,
# 5, 4, 5… What is shown counts down instead. Good news is taken at once; bad
# news is eased in over a couple of minutes, so a run that really is behind
# still says so, but one slow call doesn't make the number jump.
EASE_SECONDS = 120


def _steady(request_id: str, remaining, now: float):
    with _lock:
        original = _entries.get(request_id)
        if original is None:
            return remaining
        if remaining is None:
            original.pop("_shown", None)
            return None
        shown, at = original.get("_shown"), original.get("_shownAt", now)
        if shown is None:
            value = remaining
        else:
            counted = max(0.0, shown - (now - at))
            if remaining <= counted:
                value = remaining
            else:
                value = counted + (remaining - counted) * (1 - math.exp(-(now - at) / EASE_SECONDS))
        original["_shown"], original["_shownAt"] = value, now
        return value


def _fraction(entry: dict, creep: bool = True) -> float:
    """How far through, by stage weight — with a creep inside the current stage.

    Between stages the bar only moves when work actually completes. Inside one,
    there is nothing to count: planning is a single call that takes a minute and
    a half and reports nothing until it returns. Sitting at 0% for ninety
    seconds reads as "frozen", so the bar eases toward the end of its stage
    without ever reaching it — it can overstate how close a stage is to
    finishing, but it can never claim a stage is done when it isn't.
    """
    mode = MODES[entry["mode"]]
    done = 0.0
    for stage in mode["order"]:
        weight = mode["weights"][stage]
        if stage != entry["stage"]:
            done += weight
            continue

        within = entry["done"] / max(1, entry["total"])
        if within == 0 and not creep:
            break
        if within == 0:
            # No batch has landed yet: ease across the share of this stage that
            # one unit of work represents, approaching but never reaching it.
            unit = weight / max(1, entry["total"])
            in_stage = time.time() - entry.get("stageStartedAt", entry["startedAt"])
            expected = mode["seconds"].get(stage, 60)
            within_creep = 1 - 2 ** (-in_stage / max(5, expected))
            done += unit * within_creep
        else:
            done += weight * within
        break
    return min(0.99, done)


def _sweep() -> None:
    now = time.time()
    with _lock:
        for key in [k for k, v in _entries.items()
                    if (v["finishedAt"] and v["finishedAt"] < now - TTL_SECONDS)
                    or v["startedAt"] < now - RUNNING_TTL_SECONDS]:
            _entries.pop(key, None)
