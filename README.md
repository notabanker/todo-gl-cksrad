# TYCHE

TYCHE is a local-first todo app built around a carnival-style decision wheel.
Add tasks, assign priorities, and let the server choose the next task with an
age-weighted random spin: the longer a task has waited, the better its odds.

The interface is a compact desktop dashboard with the wheel and task list side
by side. It includes a full-screen jackpot reveal, strong fairground colors,
responsive light and dark themes, and reduced-motion support.

## Features

- Add, rename, reprioritize, and delete tasks.
- Server-authoritative, age-weighted winner selection.
- Animated wheel with the Spin button in its center.
- Full-window jackpot celebration for the selected task.
- Persistent Done list with reversible checkboxes.
- Completion check-pop, glow, spark burst, and automatic Done-list scrolling.
- Compact 1060×720 macOS window with an 840×560 minimum layout.
- No frontend framework, CDN, web font, or external network dependency.

## Run locally

```bash
./run.sh
```

The launcher creates `.venv` when needed, installs the Python dependencies, and
starts TYCHE at [http://127.0.0.1:8000](http://127.0.0.1:8000).

For a manual setup:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Task data is stored in the repo-local `tyche.db`, which is intentionally ignored
by Git.

## Build the standalone macOS app

TYCHE includes a native AppKit/WKWebView shell and a bundled FastAPI backend, so
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
dist/TYCHE.app
dist/TYCHE-macOS-arm64.zip
```

The build is ad-hoc signed for local use. Public distribution without
Gatekeeper warnings requires a Developer ID signature and Apple notarization.
Standalone app data lives at:

```text
~/Library/Application Support/TYCHE/tyche.sqlite3
```

If a repo-local `tyche.db` exists while building, it is included as optional
starter data for the first app launch.

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
| `GET` | `/wheel/spin` | Return the server-selected open task or `null` |

The client never chooses its own winner. It requests `/wheel/spin`, finds the
returned task by ID in the current wheel snapshot, and animates the wheel to
that exact segment.

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

The suite covers API persistence and validation, completion/reopening, exclusion
of completed tasks from the wheel, and the weighted-selection distribution.

## Project layout

```text
main.py                 FastAPI routes and request validation
models.py               SQLModel todo schema and UTC timestamp helper
database.py             SQLite engine and session wiring
wheel.py                Pure weighted-selection logic
templates/index.html    Complete vanilla HTML/CSS/JavaScript interface
desktop_backend.py      Entrypoint for the bundled backend
macos/                  Native AppKit/WKWebView application sources
build_macos.sh          Reproducible Apple Silicon app builder
tests/                  API and weighted-selection tests
DESIGN_BRIEF.md         Product, motion, accessibility, and API guardrails
```
