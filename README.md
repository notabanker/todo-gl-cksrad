# To-Do Gambling

If it’s done in under 2 minutes, do it directly! The dopamine catch for ADHD
people who have a shit ton to do.

To-Do Gambling is a local-first todo app built around a carnival-style decision wheel.
Add tasks, assign priorities, and let the server choose the next task with an
age-weighted random spin: the longer a task has waited, the better its odds.

The compact, tabbed dashboard separates **Heute**, **Glücksrad**, and
**Rewards** while keeping the active task board visible at a glance. It also
includes a live day-pressure clock, a full-screen jackpot reveal, strong
fairground colors, an eggshell-and-black interface, and reduced-motion support.

## Features

- Add, rename, reprioritize, and delete tasks.
- Server-authoritative, age-weighted winner selection.
- Animated wheel with the Spin button in its center.
- Full-window jackpot celebration for the selected task.
- Persistent Done list with reversible one-off tasks and final Daily completions.
- Completion check-pop, glow, spark burst, and automatic Done-list scrolling.
- Compact task rows with a one-click **Morgen** action and a separate signed
  Daily Score.
- Daily recurring tasks with streak bonuses and one immutable occurrence per day.
- Monotonic lifetime XP, levels, daily wins, streaks, and completion rate.
- Live local clock and end-of-day countdown with workload-scaled green, yellow,
  and red pressure states.
- A focused pressure pulse when the day becomes critical and once per new
  20-minute block while it remains urgent.
- Fixed, independently scrollable Open and Done task panes.
- No-loss Dopamine Slots 2.0: lifetime 100-XP milestones earn Lucky Tickets;
  every accepted server-selected result pays Reward Chips.
- User-defined real-world reward goals with atomic redemption and history.
- Live green/yellow/red task-age badges in total hours and minutes.
- Compact 1060×720 macOS window with an 840×560 minimum layout.
- No frontend framework, CDN, web font, or external network dependency.

## Run locally

```bash
./run.sh
```

The launcher creates `.venv` when needed, installs the Python dependencies, and
starts To-Do Gambling at [http://127.0.0.1:8000](http://127.0.0.1:8000).

For a manual setup:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Task and arcade state are stored in the repo-local `tyche.db`, which is
intentionally ignored by Git.

## Productivity rewards

Completing a task for the first time awards XP based on its priority: P1 earns
50 XP, P2 earns 30 XP, and P3 earns 20 XP. A capped backlog bonus adds 2 XP for
every full available day the task waited, up to 30 extra XP. Time while a task
is postponed is excluded. Every 250 lifetime XP advances one display level.

The compact KPI strip tracks lifetime XP, today's completed tasks, signed Daily
Score, the completion streak, completion rate, level, and progress toward the
next level. Lifetime XP is recorded in an append-only ledger: reopening or
deleting a completed one-off never confiscates XP, while completing it again
cannot award XP twice.

Daily Score is intentionally separate. Completing work raises it; postponing a
task to tomorrow applies `-10` once for that task and local day, removes it from
today's board, wheel, completion rate, and EOD pressure, and never subtracts
lifetime XP. Lucky Tickets already minted at 100-XP boundaries remain earned,
and an accounted boundary never mints twice.

## Daily routines

A routine materializes at most one task occurrence for each scheduled local
day. Daily XP is the priority base plus one XP per current streak day, capped at
10 bonus XP. Dailies never earn a waiting-age bonus. Missing or postponing an
occurrence breaks the next streak; previous lifetime XP remains untouched.

Daily completions are final so reopening cannot farm a streak or a second XP
award. Pause, reactivate, rename, or reprioritize a routine from **Heute →
Tägliche Routinen → Verwalten**.

## Day pressure

The clock uses the Mac or browser's local time and counts down to the next local
midnight. With open tasks, **Getting tight** begins when two hours or less remain
or the workload falls to 45 minutes per task. **Critical** begins when 30 minutes
or less remain or the workload falls to 20 minutes per task. Zero open tasks is
**All clear**; every less urgent state is **On track**.

Critical mode pulses once on entry and once in each new local 20-minute block
while the app is visible. It pauses in the background, defers during a wheel spin
or winner dialog, and becomes a static red warning when reduced motion is enabled.

## Dopamine slots

Each newly reached lifetime 100-XP boundary mints one persistent Lucky Ticket.
Pulling spends one ticket, never subtracts XP, and returns a positive focus
prompt plus Reward Chips selected by the server. At most three reward-paying
pulls are accepted per local day; a rejected fourth pull retains its ticket.
The last result persists across launches, and final symbols, payout, and jackpot
copy always come from the server.

| Result | Odds | Chips |
|---|---:|---:|
| Quick Win | 50% | 5 |
| 15-Minute Focus | 30% | 10 |
| 25-Minute Power Block | 15% | 20 |
| 5-Minute Reset | 4% | 50 |
| Focus Jackpot | 1% | 100 |

Reward Chips collect in one wallet. Create any real-world goal, set its Chip
cost, and redeem it only when the full balance is available. Awards and
redemptions are recorded atomically in an append-only history.

The compact desktop window mode (820 px tall or less) compresses the cabinet to
a mini three-reel strip so the wheel, day clock, slots, and task board all remain
usable at once.

## Build the standalone macOS app

To-Do Gambling includes a native AppKit/WKWebView shell and a bundled FastAPI backend, so
the finished app does not require Python, Terminal, or a separate browser.

Requirements:

- Apple Silicon Mac
- Apple Command Line Tools
- [`uv`](https://docs.astral.sh/uv/)

Build it with:

```bash
./build_macos.sh
```

Generated artifacts:

```text
dist/To-Do Gambling.app
dist/To-Do-Gambling-macOS-arm64.zip
```

The build is ad-hoc signed for local use. Public distribution without
Gatekeeper warnings requires a Developer ID signature and Apple notarization.
Standalone app data intentionally stays in the original compatibility path, so
updating from TYCHE does not lose existing tasks, Lucky Tickets, the last slot
result, or reward history:

```text
~/Library/Application Support/TYCHE/tyche.sqlite3
```

The repo-local `tyche.db` is never copied into the application bundle. A first
launch creates a clean database, while an existing standalone installation keeps
using its private Application Support database. Every file-backed database is
hardened to mode `0600` before SQLite connects or writes. Schema upgrades are
versioned, transactional, and create a verified mode-`0600` sibling backup
before changing an existing legacy database.

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | Render the application |
| `GET` | `/healthz` | Native launcher readiness check |
| `GET` | `/todos` | List open tasks |
| `GET` | `/todos?status=done` | List completed tasks, newest first |
| `GET` | `/today` | List currently actionable tasks and materialize today's Dailies |
| `GET` | `/tomorrow` | Preview postponed tasks and tomorrow's Daily occurrences |
| `GET` | `/dailies?date=YYYY-MM-DD` | List Daily occurrences for today or tomorrow |
| `POST` | `/todos` | Create a task from `{title, priority}` |
| `PATCH` | `/todos/{id}` | Update title, priority, or `open`/`done` status |
| `DELETE` | `/todos/{id}` | Delete a task |
| `POST` | `/todos/{id}/postpone` | Move an open task to tomorrow and apply today's `-10` score once |
| `GET/POST` | `/routines` | List or create Daily routine templates |
| `PATCH/DELETE` | `/routines/{id}` | Edit, pause, or reactivate a routine |
| `GET` | `/metrics` | Return ledger XP, Daily Score, streak, task, ticket, Chip, and spin KPIs |
| `GET/PATCH` | `/settings` | Read or update the IANA scoring timezone |
| `GET` | `/arcade` | Return XP, tickets, Chips, daily pull limit, and persisted last result |
| `POST` | `/arcade/spin` | Spend one ticket and return the server-selected symbols and Chip payout |
| `GET` | `/rewards` | Return wallet, goals, progress, and recent transactions |
| `POST` | `/rewards/goals` | Create a reward goal |
| `PATCH/DELETE` | `/rewards/goals/{id}` | Edit or archive a reward goal |
| `POST` | `/rewards/goals/{id}/redeem` | Atomically redeem an affordable goal |
| `GET` | `/rewards/history` | Return immutable Chip awards and redemptions |
| `GET` | `/wheel/spin` | Return the server-selected open task or `null` |

The client never chooses its own winner. It requests `/wheel/spin`, finds the
returned task by ID in the current wheel snapshot, and animates the wheel to
that exact segment.

The same rule applies to Dopamine slots: the client may animate temporary reel
symbols, but it always finishes on the symbols and result returned by
`POST /arcade/spin`.

## Weighting

A task's weight is:

```text
max(1, (now - created_at).days)
```

Age is measured in whole days and floored at one, so every open task remains
eligible while older tasks become proportionally more likely to win.

## Tests

```bash
PYTHONPATH=. .venv/bin/pytest -q
```

The suite covers legacy migration and idempotent restart, API validation,
postponement, recurring streaks and DST boundaries, monotonic XP, concurrent
completion, server-authoritative slot payouts and daily caps, atomic reward
redemption, and weighted wheel selection.

## Project layout

```text
main.py                 FastAPI routes and request validation
models.py               SQLModel todo and persistent arcade-state schemas
database.py             SQLite engine and session wiring
migrations.py           Versioned SQLite migration and verified backup runner
wheel.py                Pure weighted-selection logic
templates/index.html    Complete vanilla HTML/CSS/JavaScript interface
desktop_backend.py      Entrypoint for the bundled backend
macos/                  Native AppKit/WKWebView application sources
build_macos.sh          Reproducible Apple Silicon app builder
tests/                  API, arcade, and weighted-selection tests
DESIGN_BRIEF.md         Product, motion, accessibility, and API guardrails
```
