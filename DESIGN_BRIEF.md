# Design Brief — TYCHE frontend redesign (prompt for a coding agent)

> Paste everything below the line into the coding LLM. It is self-contained.

---

## Your role

You are a senior product designer + frontend engineer. Redesign the UI of an
existing, working web app called **TYCHE** to a clean, modern, **Apple-like**
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

| Method | Path            | Body / returns |
|--------|-----------------|----------------|
| GET    | `/todos`        | → JSON array of tasks, pre-ordered by priority then age |
| POST   | `/todos`        | body `{ "title": string, "priority": 1\|2\|3 }` → created task (201) |
| PATCH  | `/todos/{id}`   | body `{ "title"?: string, "priority"?: 1\|2\|3 }` → updated task |
| DELETE | `/todos/{id}`   | → 204 |
| GET    | `/wheel/spin`   | → the chosen task object, or `null` if there are no tasks |

Task object fields you can use: `id` (int), `title` (string), `priority`
(1=high, 2=medium, 3=low), `created_at` (ISO datetime string). Other fields
(`description`, `tags`, `status`, `updated_at`, `completed_at`) exist but aren't
needed in the UI.

**Wheel semantics you must preserve:** the *server* picks the winner (age-weighted);
the animation must **land the pointer on the task the server returned** (match by
`id`), never pick its own winner. Segments are currently equal-sized — keep them
equal-sized unless you have a clean reason otherwise.

## Current UI inventory (what exists, all must remain functional)

1. Header: title "TYCHE" + one-line subtitle.
2. **Add** form: text input (`Task name`) + priority `<select>` (P1/P2/P3) + Add button.
   Adding is AJAX (no reload); on success the input clears & refocuses and the
   list + wheel update.
3. **Wheel**: a `<canvas>` (~340px) with one colored segment per task and a
   pointer at the top; a **Play** button below it; a result line that shows the
   winning task after a spin.
4. **Tasks** list: each row shows the title, a priority dropdown (live-updates via
   PATCH), an **Edit** button (inline rename), and a **Delete** button.
   Empty state: "No tasks yet."

## Design direction (make it feel Apple-grade)

- **Typography:** system font. Establish a real type scale (e.g. large rounded
  display title, medium section labels, comfortable body). Use weight and size
  for hierarchy, not decoration. Tighten letter-spacing on large headings.
- **Color & theming:** a calm, neutral base (near-white / true-dark surfaces) with
  **one** confident accent color. Support **light and dark mode** via
  `prefers-color-scheme`, plus a `:root[data-theme=...]` override hook. Replace the
  raw rainbow HSL wheel with a **refined, harmonious palette** — e.g. a curated set
  of 6–10 tasteful hues, or tints of the accent — so a full wheel looks designed,
  not random. Priorities should read clearly (P1 high → P3 low) with subtle,
  non-garish color/label treatment.
- **Layout & spacing:** a centered, comfortably narrow column. Consistent spacing
  scale (4/8px rhythm). Group the add form, wheel, and list into clean "cards" or
  well-separated sections with generous padding.
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
  glows, the result text scales/fades in, maybe a restrained confetti burst.
  Keep it classy, not noisy.
- **Theme + state changes** should cross-fade rather than snap.
- **Respect `prefers-reduced-motion: reduce`:** drop non-essential animation, keep
  the wheel result instantaneous or minimal, no confetti.

## Accessibility (required)

- Sufficient color contrast in both themes (WCAG AA).
- Full keyboard operability; visible focus states; logical tab order.
- Buttons/inputs have accessible names; the wheel result is announced (e.g.
  `aria-live="polite"` on the result region).
- Don't rely on color alone to convey priority (include the P1/P2/P3 label).

## Deliverables

1. A redesigned `templates/index.html` (and optionally `static/` assets + the one
   allowed `main.py` mount line) implementing all of the above.
2. All existing functionality intact: add, list, inline edit, delete, priority
   change (live PATCH), spin landing on the server's winner, empty state.
3. Light and dark mode.
4. The animation set described above, with reduced-motion fallback.

## Definition of done / acceptance

- App still starts with `./run.sh` (or `uvicorn main:app`) and loads at
  `http://127.0.0.1:8000/` with **zero console errors** and **zero external
  network requests**.
- I can: add a task (animated in, input clears) → change its priority (list
  reorders) → edit its title inline → delete it (animated out) → add a few and
  press **Play** → the wheel spins smoothly and lands on the highlighted winner,
  matching the task shown in the result line.
- Toggling OS light/dark restyles the app cleanly.
- With reduced-motion enabled, everything still works without heavy animation.
- It looks like a polished Apple product, not a prototype.

## Do NOT

- Do not change the API, the Python logic, the DB, or the wheel's weighting.
- Do not add frameworks, bundlers, CDNs, web fonts, or any external requests.
- Do not let the client pick the spin winner — always land on the server's result.
