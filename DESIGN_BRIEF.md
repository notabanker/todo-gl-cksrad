# Design Brief — To-Do Gambling frontend redesign (prompt for a coding agent)

> Paste everything below the line into the coding LLM. It is self-contained.

---

## Your role

You are a senior product designer + frontend engineer. Redesign the UI of an
existing, working web app called **To-Do Gambling** to a clean, modern, **Apple-like**
standard (think Apple Human Interface Guidelines: restraint, hierarchy, generous
whitespace, precise typography, tasteful depth, and fluid, physical motion).

The current UI is functional but visually crude. **Do not change any behavior or
break the API** — this is a pure look-and-feel + motion pass.

## What the app does

A single-user "todo wheel of fortune." You add tasks (each with a priority 1–3),
they appear in a list and as colored segments on a spinning wheel. Pressing
**Play** spins the wheel and lands on a randomly chosen task, weighted so that
tasks which have waited longer are more likely to win. It's meant to feel
playful but premium.

## Tech stack & hard constraints

- Backend: **FastAPI + Jinja2**, served at `GET /`. Do **not** modify Python files.
- Frontend today is a **single Jinja2 template**: `templates/index.html`
  (inline `<style>` + vanilla `<script>` + a `<canvas>` wheel).
- **No frontend frameworks, no build step, no external network requests.** The
  app runs offline on localhost. That means: **no CDNs, no Google Fonts, no npm.**
  Use the **system font stack** (`-apple-system, BlinkMacSystemFont, "SF Pro Text",
  system-ui, sans-serif`) — on macOS this already gives you San Francisco.
- Everything must stay **self-contained**: inline CSS/JS in `index.html`, or add
  files under a new `static/` dir served by FastAPI — but if you add `static/`,
  you must also add the one line to mount it (see "Allowed backend touch" below).
  Simplest acceptable path: keep it all in `index.html`.
- Must keep running via the existing launcher (`./run.sh`) and `uvicorn main:app`.

### Allowed backend touch (only if you add static assets)

The single permitted Python edit is mounting a static dir. If (and only if) you
split CSS/JS into files, add to `main.py`:

```python
from fastapi.staticfiles import StaticFiles
app.mount("/static", StaticFiles(directory="static"), name="static")
```

Otherwise do not touch any `.py` file.

## API contract (must keep working exactly as-is)

The JS must keep talking to these endpoints. Shapes are fixed:

| Method | Path                  | Body / returns |
|--------|-----------------------|----------------|
| GET    | `/todos`              | → JSON array of open tasks, pre-ordered by priority then age |
| GET    | `/todos?status=done`  | → JSON array of completed tasks, newest first |
| POST   | `/todos`              | body `{ "title": string, "priority": 1\|2\|3 }` → created task (201) |
| PATCH  | `/todos/{id}`         | body `{ "title"?: string, "priority"?: 1\|2\|3, "status"?: "open"\|"done" }` → updated task |
| DELETE | `/todos/{id}`         | → 204 |
| GET    | `/wheel/spin`         | → the chosen open task object, or `null` if there are no tasks |
| GET    | `/arcade`             | → `{ "tickets": int, "task_xp": int, "xp_to_next_ticket": int, "last_spin": object\|null }` |
| POST   | `/arcade/spin`        | → `{ "symbols": string[3], "outcome": string, "label": string, "message": string, "tier": string, "spin_count": int, "spun_at": datetime, "remaining_tickets": int }`; 409 without a ticket |

Task object fields used by the UI: `id` (int), `title` (string), `priority`
(1=high, 2=medium, 3=low), `created_at` (ISO datetime string), `status`
(`open` or `done`), and `completed_at` (ISO datetime string or null). Other
fields include `description`, `tags`, and `updated_at`.

**Wheel semantics you must preserve:** the *server* picks the winner (age-weighted);
the animation must **land the pointer on the task the server returned** (match by
`id`), never pick its own winner. Segments are currently equal-sized — keep them
equal-sized unless you have a clean reason otherwise.

**Arcade semantics you must preserve:** the *server* also picks the final slot
outcome. The client may show transient symbols during the reel animation, but the
three final symbols, label, message, and tier must match `/arcade/spin`. A pull
spends one Lucky Ticket but never subtracts task XP. Previously crossed lifetime
100-XP boundaries must never mint again after reopening or deleting work.

## Current UI inventory (what exists, all must remain functional)

1. Header: title "To-Do Gambling" + the product description.
2. **Add** form: text input (`Task name`) + priority `<select>` (P1/P2/P3) + Add button.
   Adding is AJAX (no reload); on success the input clears & refocuses and the
   list + wheel update.
3. **Wheel**: a `<canvas>` with one colored segment per task, a pointer at the
   top, and the **Spin** button in the center. The server-selected task opens a
   full-window jackpot reveal with an animated title and **Mark it done** action.
4. **Task board**: KPI strip for XP, today, streak, completion rate, level, and
   level progress. Open and Done are fixed, independently scrollable, labeled
   keyboard regions. Open rows include task age, priority, inline rename, and
   Delete. Done rows have reversible checked checkboxes and earned-XP badges.
5. **Day pressure**: live local clock, countdown to the next local midnight,
   open-task workload, green/yellow/red state, progress meter, and a restrained
   Critical pulse once per new 20-minute block while urgent.
6. **Dopamine slots**: persistent Lucky Ticket balance, three animated reels,
   server-selected positive focus result, ticket progress, and no-loss/odds copy.

## Design direction (make it feel Apple-grade)

- **Typography:** system font. Establish a real type scale (e.g. large rounded
  display title, medium section labels, comfortable body). Use weight and size
  for hierarchy, not decoration. Tighten letter-spacing on large headings.
- **Color & theming:** use a warm white/eggshell canvas with black typography and
  controls as the main accent. Keep the wheel and reward moments saturated with
  strong fairground red, blue, yellow, green, orange, and purple. Do not switch
  the main interface to a black/dark theme. Priorities should read clearly (P1
  high → P3 low) with explicit labels as well as color.
- **Layout & spacing:** a compact two-column desktop dashboard: wheel, day
  pressure, and arcade on the left; fixed task board on the right. Stack
  responsively on narrow screens. Open and Done retain independent scroll
  regions. Use a consistent 4/8px rhythm and clean cards with generous padding.
- **Materials & depth:** soft, layered surfaces — gentle shadows, ~12–20px corner
  radii, hairline borders, optional subtle translucency/blur for bars. Avoid heavy
  drop shadows and pure-black borders.
- **Controls:** redesign inputs, the priority selector, and buttons to feel native
  and tactile — clear default/hover/active/focus/disabled states, visible but
  elegant focus rings (keyboard accessible), pill or rounded-rect buttons, a
  primary style for Add/Play and quiet styles for Edit/Delete.
- **The wheel is the hero.** Make it beautiful: crisp anti-aliased segments (canvas
  or a CSS conic-gradient approach), a polished pointer/ticker, a subtle hub/center
  cap, and a soft rim. On small task counts and large counts alike it should look
  intentional. Handle label overflow gracefully (truncate/scale).

## Motion & animation spec (this is a core deliverable)

Use CSS transitions/transforms and the **Web Animations API** / `requestAnimationFrame`.
Prefer transform/opacity (GPU-friendly). Suggested character:

- **Micro-interactions (120–260ms):** button press (slight scale-down + shadow
  change), hover elevation, input focus ring grow, priority-change feedback. Use
  spring-like easing, e.g. `cubic-bezier(0.22, 1, 0.36, 1)`.
- **Add task:** the new list row and new wheel segment should **animate in**
  (fade + slide/scale), not pop. Input should have a satisfying confirm.
- **Delete task:** row animates **out** (fade + collapse height), wheel re-renders
  smoothly.
- **Wheel spin (~3.5–4.5s):** realistic acceleration → long deceleration that
  **settles onto the server's winning segment**, ideally with a tiny overshoot/
  settle at the end. Optional: a faint tick as segments pass the pointer.
- **Winner reveal:** celebrate tastefully — e.g. the winning segment highlights/
  glows and a full-window jackpot moment animates both the panel and task title.
- **Complete task:** animate the selected task into the Done pane with a check
  pop, glow/spark burst, XP reward token, and KPI count-up.
- **Day pressure:** Critical mode may use one restrained shake/pulse on entry and
  in each new 20-minute wall-clock block. Never flash or take over the screen.
- **Slot reels:** animate transient symbols, then land on the exact final symbols
  returned by the server. Never choose an outcome locally.
- **Theme + state changes** should cross-fade rather than snap.
- **Respect `prefers-reduced-motion: reduce`:** drop non-essential animation, keep
  the wheel and slot result instantaneous or minimal, no confetti, and show a
  static red Critical warning instead of an angry pulse.

## Accessibility (required)

- Sufficient color contrast throughout the eggshell interface (WCAG AA).
- Full keyboard operability; visible focus states; logical tab order.
- Buttons/inputs have accessible names; the wheel result is announced (e.g.
  `aria-live="polite"` on the result region).
- Don't rely on color alone to convey priority (include the P1/P2/P3 label).
- Do not make the clock's per-second digits an `aria-live` region. Announce only
  meaningful urgency transitions and 20-minute Critical alerts.
- Both task scroll panes must be keyboard-focusable, labeled regions.

## Deliverables

1. A redesigned `templates/index.html` (and optionally `static/` assets + the one
   allowed `main.py` mount line) implementing all of the above.
2. All existing functionality intact: add, list, inline edit, delete, priority
   change, completion/reopen, independent Open/Done scrolling, EOD pressure,
   Lucky Ticket mint/spend, spin landing on the server's winner, and empty states.
3. A polished eggshell-and-black interface.
4. The animation set described above, with reduced-motion fallback.

## Definition of done / acceptance

- App still starts with `./run.sh` (or `uvicorn main:app`) and loads at
  `http://127.0.0.1:8000/` with **zero console errors** and **zero external
  network requests**.
- I can: add a task (animated in, input clears) → change its priority (list
  reorders) → edit its title inline → delete it (animated out) → add a few and
  press **Spin** → the wheel lands on the returned winner → mark it done → see it
  animate into Done → reopen it from its checkbox. Open and Done scroll without
  moving each other.
- EOD thresholds match the documented workload logic, Critical alerts never run
  more than once per local 20-minute block, and reduced motion stays static.
- Reaching a new lifetime 100-XP boundary mints one ticket. A pull spends exactly
  one ticket without reducing task XP, cannot re-mint an old boundary, and ends
  on the exact server-returned symbols and result.
- The eggshell interface remains readable regardless of the OS appearance.
- With reduced-motion enabled, everything still works without heavy animation.
- It looks like a polished Apple product, not a prototype.

## Do NOT

- Do not change the API, the Python logic, the DB, or the wheel's weighting.
- Do not add frameworks, bundlers, CDNs, web fonts, or any external requests.
- Do not let the client pick the spin winner — always land on the server's result.
- Do not let the client pick the arcade outcome or substitute final reel symbols.
- Do not spend or subtract task XP on a slot pull.
- Do not re-mint a Lucky Ticket for a previously crossed XP boundary.
- Do not merge Open and Done back into one scroll container.
