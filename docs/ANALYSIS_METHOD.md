# Analysis method

How `who-stresses-me-out` turns raw heart rate and logged context into an **association report**.
This document mirrors [`analyze.py`](../analyze.py) and [`report.py`](../report.py).

> **Read this first.** Everything below produces *associations*, not causes, and a heart-rate signal,
> not a clinical measurement. The method is engineered to be **conservative** — to make weak or
> confounded findings look weak — not to maximise dramatic-looking numbers.

There are two complementary analyses:

1. **Minute-level** — an intra-meeting heart-rate elevation proxy (needs the unofficial HR source).
2. **Day-level** — next-morning recovery / HRV / resting-HR changes (needs only the official API).

The day-level analysis works entirely on its own, so you get a signal even with day-level data only.

---

## 1. Minute-level heart-rate proxy

WHOOP's official API does not expose the in-app Stress Monitor, so "signal" here is defined as **how
far above your personal resting baseline your heart rate sits during a meeting, when you are not
physically active**.

### 1.1 The meeting window

- The window runs from `ts_start` to `ts_end`. If a meeting was never explicitly stopped, `ts_end`
  defaults to `ts_start + DEFAULT_WINDOW_MIN` and is capped at `ts_start + MAX_WINDOW_MIN`.
- **Arrival trim:** the first `min(TRIM_MINUTES, window_length / 3)` minutes are dropped, because
  walking in and settling down inflates heart rate for reasons unrelated to the meeting.
- **Workout exclusion:** any minute that falls inside an official WHOOP workout window is removed, so
  a gym session inside the window can't be read as stress.
- The window needs at least `MIN_EVENT_SAMPLES` surviving samples; otherwise the event is skipped
  (it becomes part of *missing HR*).

### 1.2 The baseline (pre-meeting, awake-only)

The baseline is your **resting** heart rate estimated close to the meeting, using **awake hours only**
(local hour 07:00–23:00) so sleep doesn't drag it down. It is computed with graceful fallbacks:

| Method | When used | Statistic |
| --- | --- | --- |
| `pre-window` | ≥ `MIN_BASELINE_SAMPLES` awake samples in the `BASELINE_PRE_MIN` minutes before the meeting | 25th percentile |
| `awake-day` | not enough pre-window data, but enough awake samples that day | 20th percentile |
| `whole-day` | only sparse data available | 25th percentile of the day |
| *(none)* | no usable samples | event skipped |

A low percentile (rather than the mean) is used so the baseline reflects genuine rest, not the day's
activity.

### 1.3 The window statistic and elevation

- The meeting's heart rate is summarised by its **median** (robust to a few spikes), not the mean.
- **Primary signal — `elev` = median − baseline.** This is the number that drives ranking.
- Also recorded: `elev_peak` (peak − baseline) and `pct_above` (share of samples above
  `baseline + ELEVATION_THRESHOLD_BPM`).

### 1.4 Matched control (a cross-check, not the primary signal)

To guard against "it's just that time of day", each meeting is compared against a **matched control**:
your heart rate at the **same weekday and time of day** (within `MATCHED_CONTROL_HALFWIN_MIN` minutes),
on **past, non-meeting** times, excluding other logged event windows. When enough matched samples
exist (≥ `MIN_BASELINE_SAMPLES`), a control baseline (25th percentile) yields `control_elev`
= median − control-baseline. If there isn't enough matched data, the report simply falls back to the
resting-baseline elevation; the matched control is a corroborating cross-check, never a replacement.

---

## 2. Grouping, ranking, and shrinkage

Analyzed events are grouped three ways — by **person · context** (`tag`), by **person**, and by
**location** — and each group gets a summary.

- **Averages:** `avg_elev` (mean of members' elevations) and `median_elev`.
- **Small-sample shrinkage:** the ranking value is an **adjusted** elevation that pulls the group mean
  toward the global mean by `SHRINK_K` pseudo-observations:

  ```
  adj_elev = (n * avg_elev + SHRINK_K * global_mean) / (n + SHRINK_K)
  ```

  So a context with two loud meetings can't outrank a stable context with many. Shrinkage is used for
  **ranking order**; it does **not** replace the uncertainty layer below.
- **Coverage:** `n_analyzed / n_logged` for that group — how many of your logged meetings actually
  matched heart-rate data.
- **Group fraction & confounded fraction:** the share of a group's events that are multi-participant,
  and the share flagged with a confounder.

---

## 3. Uncertainty: bootstrap confidence intervals

Each group's elevations are run through a **deterministic percentile bootstrap of the mean**:

- Resample the group's elevations with replacement `BOOTSTRAP_N` times (default 1000).
- Report the 2.5th and 97.5th percentiles of the resampled means as a **95% CI**, rounded to 0.1 BPM.
- The bootstrap uses a **fixed seed** (`BOOTSTRAP_SEED`), so results are reproducible and testable.
- Groups with fewer than 2 values get **no CI** (and cannot rise above *weak*).

A CI that **excludes zero** is the strongest evidence the tool offers that an effect is real rather
than noise.

---

## 4. Evidence levels

Every group is labelled with one of four levels, weakest to strongest:

| Level | Assigned when |
| --- | --- |
| **insufficient** | fewer than 3 events, **or** coverage below `MIN_COVERAGE` |
| **weak** | fewer than 5 events, **or** no CI, **or** CI wider than `WIDE_CI_BPM` |
| **emerging** | enough data and a reasonably tight CI, but the CI still spans zero |
| **consistent** | the CI **excludes zero** (a directionally clear effect) |

**Confounder cap:** if more than `CONFOUNDER_FRAC` of a group's events are flagged
(caffeine `high` / alcohol / illness / commute), any *emerging* or *consistent* level is **capped
back to weak**. Heavy confounding can never present as a strong finding.

In the report, only *weak / emerging / consistent* appear in the ranked sections; *insufficient*
groups are collected under **"Needs more data"**, and contexts with a **negative** elevation and
*emerging/consistent* evidence get their own **"associated with a lower heart-rate response"**
section.

---

## 5. Multi-participant events: limited attribution

A meeting can have several participants (`event_participants`). The first participant is treated as
**primary** and carries the `person · context` label, but the event is flagged `is_group`. Any group
whose events include group meetings is annotated **"group context — limited per-person attribution"**
in the report. The tool deliberately does **not** try to split a shared signal across individuals — a
group heart-rate response cannot be pinned on one person.

---

## 6. Day-level analysis (official API)

Stress often shows up **overnight**, so this complementary signal needs only official WHOOP data.

- **Personal baselines:** your average `recovery`, `hrv`, `rhr`, `strain`, and `sleep_perf` across all
  synced days.
- **Per context:** for each meeting, compare the **next morning's** recovery (and HRV / resting HR)
  against your personal baseline, plus that **same day's** strain and sleep performance.
- **Recovery deficit** = baseline recovery − next-morning recovery (a positive deficit = worse
  recovery after seeing them). It is shrunk toward zero by `SHRINK_K`, gets a bootstrap CI when there
  are ≥ 2 matched days, and is assigned an evidence level exactly as above.
- Results are ranked by adjusted deficit and shown under **"Day-level (official WHOOP)"**.

---

## 7. Configuration knobs

All thresholds live in [`config.py`](../config.py) and can be overridden via `.env`:

| Setting | Default | Controls |
| --- | --- | --- |
| `DEFAULT_WINDOW_MIN` / `MAX_WINDOW_MIN` | 90 / 240 | Meeting window length and cap (minutes) |
| `TRIM_MINUTES` | 10 | Arrival minutes trimmed from the window start |
| `BASELINE_PRE_MIN` | 90 | Pre-meeting resting window (minutes) |
| `MIN_BASELINE_SAMPLES` / `MIN_EVENT_SAMPLES` | 10 / 5 | Minimum samples for a baseline / an event |
| `ELEVATION_THRESHOLD_BPM` | 12 | Threshold for the `pct_above` metric |
| `SHRINK_K` | 2 | Small-sample shrinkage strength |
| `BOOTSTRAP_N` / `BOOTSTRAP_SEED` | 1000 / 1234 | Bootstrap resamples / fixed seed |
| `MIN_COVERAGE` | 0.5 | Minimum HR-matched fraction for usable evidence |
| `WIDE_CI_BPM` | 20 | CI width above which evidence is capped at *weak* |
| `CONFOUNDER_FRAC` | 0.5 | Confounded fraction above which evidence is capped at *weak* |
| `MATCHED_CONTROL_HALFWIN_MIN` | 90 | Time-of-day half-window for matched controls (minutes) |

---

## 8. What the method is — and is not

- **It is** a conservative, reproducible way to surface contexts **associated** with a higher or lower
  heart-rate response, with honest evidence levels and uncertainty intervals.
- **It is not** a measure of causation, and not a clinical, psychological, or medical assessment. A
  higher heart rate has many causes the tool cannot isolate. Evidence levels and CIs describe
  *statistical* confidence in an *association* — nothing more.
