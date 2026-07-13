# To-Do Gambling

If it’s done in under 2 minutes, do it directly! The dopamine catch for ADHD
people who have a shit ton to do.

To-Do Gambling is a local-first todo app built around a carnival-style decision wheel.
Add tasks, assign priorities, and let the server choose the next task with an
age-weighted random spin: the longer a task has waited, the better its odds.

The compact dashboard keeps the carnival wheel, live day-pressure clock, and XP
focus slots beside a task board whose Open and Done panes are fixed and
independently scrollable. It also includes a full-screen jackpot reveal, strong
fairground colors, an eggshell-and-black interface, and reduced-motion support.

## Features

- Add, rename, reprioritize, and delete tasks.
- Server-authoritative, age-weighted winner selection.
- Animated wheel with the Spin button in its center.
- Full-window jackpot celebration for the selected task.
- Persistent Done list with reversible checkboxes.
- Completion check-pop, glow, spark burst, and automatic Done-list scrolling.
- Productivity rewards with XP, levels, daily wins, streaks, and completion rate.
- Live local clock and end-of-day countdown with workload-scaled green, yellow,
  and red pressure states.
- A focused pressure pulse when the day becomes critical and once per new
  20-minute block while it remains urgent.
- Fixed, independently scrollable Open and Done task panes.
- No-loss Dopamine slots: lifetime 100-XP milestones earn Lucky Tickets for
  server-selected focus prompts.
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

Completing a task awards XP based on its priority: P1 earns 50 XP, P2 earns
30 XP, and P3 earns 20 XP. A capped backlog bonus adds 2 XP for every full day
the task waited, up to 30 extra XP. Every 250 XP advances one level.

The compact KPI strip tracks total XP, today's completed tasks, the current
daily completion streak, completion rate, level, and progress toward the next
level. XP is derived from the current Done list, so reopening a task removes its
points and completing it again cannot inflate the total beyond the completed
work currently recorded.

Current XP can therefore decrease when a completed task is reopened or deleted.
Lucky Tickets already minted at lifetime-high XP milestones remain earned, and
returning to a previously rewarded 100-XP boundary never mints it again.

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

Each newly reached lifetime-high 100-XP boundary mints one persistent Lucky
Ticket. Pulling spends one ticket, never subtracts task XP, and returns a positive
focus prompt selected by the server. Reopening or deleting completed work can
lower current XP but cannot erase minted tickets or re-mint an old boundary. The
last result persists across launches, and the final symbols always come from the
server.

| Result | Odds |
|---|---:|
| Quick Win | 50% |
| 15-Minute Focus | 30% |
| 25-Minute Power Block | 15% |
| 5-Minute Reset | 4% |
| Focus Jackpot | 1% |

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
using its private Application Support database.

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | Render the application |
| `GET` | `/healthz` | Native launcher readiness check |
| `GET` | `/todos` | List open tasks |
| `GET` | `/todos?status=done` | List completed tasks, newest first |
| `POST` | `/todos` | Create a task from `{title, priority}` |
| `PATCH` | `/todos/{id}` | Update title, priority, or `open`/`done` status |
| `DELETE` | `/todos/{id}` | Delete a task |
| `GET` | `/arcade` | Return Lucky Tickets, current task XP, XP to the next new milestone, and the last slot result |
| `POST` | `/arcade/spin` | Spend one Lucky Ticket and return the server-selected focus result; `409` when no ticket is available |
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

The suite covers API persistence and validation, completion/reopening, arcade
ticket minting and spending, lifetime-high/no-remint behavior, persisted
server-authoritative slot outcomes, exclusion of completed tasks from the wheel,
and the weighted-selection distribution.

## Project layout

```text
main.py                 FastAPI routes and request validation
models.py               SQLModel todo and persistent arcade-state schemas
database.py             SQLite engine and session wiring
wheel.py                Pure weighted-selection logic
templates/index.html    Complete vanilla HTML/CSS/JavaScript interface
desktop_backend.py      Entrypoint for the bundled backend
macos/                  Native AppKit/WKWebView application sources
build_macos.sh          Reproducible Apple Silicon app builder
tests/                  API, arcade, and weighted-selection tests
DESIGN_BRIEF.md         Product, motion, accessibility, and API guardrails
```
