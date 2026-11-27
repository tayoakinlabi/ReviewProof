# The golden log

A small, fixed review history whose metrics were **worked out by hand** from `scenario.csv`,
independently of the metrics code. The tests build a real log from the scenario, through the
public API with a fixed clock and a scripted gate, and compare the results with these tables.
All data is synthetic.

| File | Contents |
| --- | --- |
| `scenario.csv` | What happened: who was shown what, when, and what they decided |
| `expected_items.csv` | One row per decided item, worked out by hand (open it as a spreadsheet) |
| `expected_metrics.csv` | The metrics for the queue and for each reviewer |

Do not "fix" an expected value to match the code. Recalculate it by hand first.

## The scenario

Times are UTC. The queue's minimum review time is 30 seconds and a pause over 30 minutes starts
a new session. Twelve planted errors, `PE-01` to `PE-12`: omissions are 01-03, wrong
classifications 04-06, wrong values 07-09 and policy breaches 10-12. `R` rows are real items.
`P`, `Q` and `X` rows are planted items released with the real item named in `released_with`.

- Reviewer **A** decides 24 items in two sessions, on Monday and Tuesday of ISO week 10. The
  Tuesday session has 25-minute pauses, so it stays one session across four hours.
- Reviewer **B** decides 10 items on Monday of week 11, in two sessions 39 minutes apart.

Deliberate edge cases:

- `R8` is approved after exactly 30 s. That is **not** faster than the minimum. `R10`, at 29 s,
  is.
- `R13` is shown to A first, then to B, who decides. Its dwell time runs from B's showing.
- `R14` is shown to B twice. Its dwell time runs from the first showing.
- `P05` is edited, but not in its defect field: a **miss**.
- `X01` is PE-01 again for B, who met it in `Q01`. A **repeat**: not part of any score.
- `X02` and `R18` are shown and never decided: `X02` is **pending**, not a miss.
- `R19` is never shown.

## How the numbers were worked out

**Catch rate.** Counted planted items are those decided at first sight: A has 12 (P01-P12), B
has 4 (Q01, Q02, Q04, Q07). X01 is a repeat.
A caught P01, P04, P07, P08, P11, P06, P12 = **7 of 12**. B caught Q04 = **1 of 4**.
Queue: **8 of 16**.

**By class (queue).** Omission: P01 caught; P02, P03, Q01, Q02 missed = 1/5. Wrong
classification: P04, P06, Q04 caught; P05 missed = 3/4. Wrong value: P07, P08 caught; P09, Q07
missed = 2/4. Policy breach: P11, P12 caught; P10 missed = 2/3.

**Fast approvals** (approve, and dwell under 30 s, planted items included).
A approved 14: R1, R2, R3, P10, R5, P02, R6, R7, R8, P03, R10, P09, R11, R12. Fast: R3 (20),
P10 (25), R6 (15), P03 (10), R10 (29) = **5/14**.
B approved 9: R13, Q01, R14, Q02, R15, R16, Q07, R17, X01. All but R16 (40) are fast =
**8/9**. Queue: **13/23**.

**Agreement** (real items only). A: 12 decided, all approved but R4 (edit) and R9 (reject) =
**10/12**. B: R13-R17 all approved = **5/5**. Queue: **15/17**.

**Median dwell.** All 34 decided, in seconds, sorted:
4 5 5 6 6 7 8 9 10 12 15 20 25 29 30 31 **35 40** 40 40 45 50 55 60 60 65 70 75 80 85 90 95
100 120. Median = (35 + 40) / 2 = **37.5 s**.
A (24): the 12th and 13th are 50 and 55, so 52.5 s. B (10): the 5th and 6th are 6 and 7, so
6.5 s.

**Load.** Reviewer-days: A Monday 14, A Tuesday 10, B Monday 10: median 10, most 14. Sessions:
14, 10, 8 and 2 items: median (8 + 10) / 2 = 9.

**Fatigue.** A position is early if `position x 2 <= session size + 1`. So positions 1-7 of 14,
1-5 of 10, 1-4 of 8, and 1 of 2.
Early: P01, P04, P07 (3/3); P11, P03, P06 (2/3); Q01, Q02 (0/2) = **5/8**.
Late: P10, P02, P05, P08 (1/4); P09, P12 (1/2); Q04, Q07 (1/2) = **3/8**.
Drop = 0.625 - 0.375 = **0.25**.

**By position.** Positions 1-10: 3/5 (A Monday) + 3/5 (A Tuesday) + 1/4 (B) = 7/14.
Positions 11-25: P05 missed, P08 caught = 1/2.

**By hour of session**, counted from the session's first showing. Only A's Tuesday session
(start 09:59) goes past one hour: P11 10:00 and P03 10:50 are in hour 1; P06 11:40 in hour 2
(101 minutes); P09 12:30 in hour 3 (151); P12 13:20 in hour 4 (201).
Hour 1: Monday 4/7 + Tuesday 1/2 + B 1/4 = **6/13**.

**Wilson 95% intervals**, with z = 1.959964:
`(p + z²/2n ± z·sqrt(p(1-p)/n + z²/4n²)) / (1 + z²/n)`.

- 8 of 16: centre 0.5; margin 1.959964 x sqrt(0.015625 + 0.003751) / 1.240091 = 0.2200.
  So **0.2800 to 0.7200**.
- 13 of 23: centre (0.565217 + 0.083510) / 1.167020 = 0.555883; margin 1.959964 x
  sqrt(0.010685 + 0.001815) / 1.167020 = 0.187770. So **0.3681 to 0.7437**.
- 7 of 12: centre (0.583333 + 0.160061) / 1.320122 = 0.563126; margin 1.959964 x
  sqrt(0.020255 + 0.006670) / 1.320122 = 0.243609. So **0.3195 to 0.8067**.

The interval calculation is also checked against published values in `tests/test_stats.py`.
