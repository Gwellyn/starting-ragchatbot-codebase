# Frontend Changes: Dark/Light Theme Toggle

## Summary
Added a toggle button that switches the UI between the existing dark theme and a new light theme. The button is fixed to the top-right of the viewport, uses animated sun/moon icons, and is fully keyboard-accessible.

## Files Changed

### `frontend/index.html`
- Added a `#themeToggle` `<button>` as the first element in `<body>`, containing inline sun and moon SVG icons. Uses `aria-label` and `aria-pressed` for accessibility.
- Bumped the cache-busting query params for `style.css` (`v=16`) and `script.js` (`v=13`).

### `frontend/style.css`
- Added a `:root[data-theme="light"]` block defining light-theme values for all existing CSS custom properties (`--background`, `--surface`, `--text-primary`, `--border-color`, `--link-color`, etc.), so every existing component restyles automatically when the attribute is set — no component-specific overrides needed.
- Added `.theme-toggle` styles: fixed top-right circular button matching the existing surface/border/shadow language, with hover/active/focus-visible states consistent with other interactive elements (e.g. `#sendButton`).
- Cross-faded sun/moon icons inside the button using `opacity`/`transform` transitions (rotate + scale) driven by the `[data-theme="light"]` attribute, so the icon swap animates smoothly instead of jumping.
- Added `background-color`/`border-color`/`color` transitions to `body` and the main surface containers (`.sidebar`, `.chat-main`, `.chat-container`, `.chat-messages`, `.message-content`, `.chat-input-container`, `.stat-item`) so the theme switch animates smoothly across the whole page rather than only the toggle button.
- Added a small-screen size adjustment for the toggle button in the existing `768px` media query.

### `frontend/script.js`
- Added `themeToggle` to the cached DOM elements and wired a `click` listener to it in `setupEventListeners()`.
- Added theme functions:
  - `initializeTheme()` — reads a saved preference from `localStorage` (key `theme`) on load and applies it (defaults to dark if unset or unavailable).
  - `toggleTheme()` — flips between `light`/`dark` based on the current `documentElement` state.
  - `applyTheme(theme)` — sets/removes `data-theme="light"` on `<html>`, updates the button's `aria-pressed`/`aria-label`, and persists the choice to `localStorage` (wrapped in `try/catch` for environments where storage is unavailable, e.g. private browsing).

## Behavior
- Default theme is the original dark theme (no stored preference).
- Clicking the button (or focusing it via Tab and pressing Enter/Space, since it's a native `<button>`) toggles the theme and persists the choice across reloads.
- Icon shown reflects the *current* theme: moon in dark mode, sun in light mode.
- Verified visually in a browser: toggle animates smoothly, light theme has good contrast across the sidebar, chat bubbles, input, and buttons, focus ring is visible on keyboard focus, and the preference survives a page reload.

## Light Theme Palette Refinement
Revisited the `:root[data-theme="light"]` variable values in `frontend/style.css` to double-check accessibility, computing WCAG 2.1 contrast ratios for every foreground/background pairing used in the UI:

| Pairing | Ratio |
|---|---|
| `--text-primary` on `--background` / `--surface` | ~17.1:1 / ~17.9:1 |
| `--text-secondary` on `--background` / `--surface` | ~7.2:1 / ~7.6:1 |
| white button text on `--primary-color` | ~5.2:1 |
| `--link-color` / `--link-hover` on `--background` | ~6.4:1 / ~8.3:1 |
| `--border-color` on `--background` / `--surface` | ~4.6:1 / ~4.8:1 |

Changes made as a result:
- `--text-secondary`: `#52606d` → `#475569` (still comfortably passes AA, aligned to a standard slate value for consistency with the rest of the palette).
- `--border-color`: `#cbd5e1` → `#64748b`. The original value only reached ~1.4:1 against the background, below the WCAG 1.4.11 non-text contrast minimum (3:1) for UI component boundaries such as the chat input, card borders, and sidebar divider that rely on `--border-color` alone to indicate their edges. The new value clears 3:1 with margin (~4.6–4.8:1) while staying a muted, on-palette slate tone.

All body text now clears the 4.5:1 AA minimum with significant margin, and structural borders clear the 3:1 non-text minimum. Colors were re-verified in a browser after the change — sidebar dividers, card borders, and the chat input outline are now clearly visible in light mode without looking heavy, and all text remains crisp.
