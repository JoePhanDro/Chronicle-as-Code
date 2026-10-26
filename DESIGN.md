---
name: Chronicle
description: Dense, dark-first incident ledger; slate surfaces, hairline borders, colour only as signal.
colors:
  ledger-black: "#020617"
  ledger-slate: "#0f172a"
  hairline: "rgba(255, 255, 255, 0.10)"
  text: "#e2e8f0"
  text-strong: "#f1f5f9"
  text-muted: "#94a3b8"
  text-faint: "#7183a0"
  signal-sky: "#38bdf8"
  signal-sky-text: "#7dd3fc"
  dusk-violet: "#8b5cf6"
  entity-system: "#a78bfa"
  error-rose: "#fda4af"
  phase-critical: "#ef4444"
  phase-analysis: "#eab308"
  phase-containment: "#f97316"
  phase-mitigation: "#22c55e"
  phase-recovery: "#10b981"
  phase-lessons: "#3b82f6"
  phase-activity: "#94a3b8"
  status-open: "#f43f5e"
  status-in-progress: "#f59e0b"
  status-resolved: "#22c55e"
typography:
  title:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 600
    lineHeight: "1.75rem"
  body:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: "1.25rem"
  label:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 500
    lineHeight: "1rem"
  eyebrow:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "10px"
    fontWeight: 600
    letterSpacing: "0.22em"
  mono:
    fontFamily: "JetBrains Mono, ui-monospace, monospace"
    fontSize: "0.75rem"
    fontWeight: 400
rounded:
  md: "6px"
  lg: "8px"
  xl: "12px"
  "2xl": "16px"
  full: "9999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
  "2xl": "32px"
components:
  button-primary:
    backgroundColor: "{colors.signal-sky}"
    textColor: "{colors.ledger-black}"
    typography: "{typography.body}"
    rounded: "{rounded.lg}"
    padding: "8px 16px"
  button-secondary:
    backgroundColor: "transparent"
    textColor: "{colors.text}"
    rounded: "{rounded.lg}"
    padding: "6px 12px"
  input:
    backgroundColor: "{colors.ledger-black}"
    textColor: "{colors.text-strong}"
    rounded: "{rounded.lg}"
    padding: "8px 12px"
  card:
    backgroundColor: "{colors.ledger-slate}"
    textColor: "{colors.text}"
    rounded: "{rounded.xl}"
    padding: "16px"
  menu:
    backgroundColor: "{colors.ledger-slate}"
    textColor: "{colors.text}"
    rounded: "{rounded.xl}"
    padding: "4px"
---

# Design System: Chronicle

## Overview

**Creative North Star: "The Incident Ledger"**

Chronicle looks like a well-kept log: near-black slate surfaces, one-pixel hairlines, small sober type, and colour that appears only when it means something (a phase, a status, an entity kind). The interface stays out of the way of the timeline and graph, which are the content. Density is a feature; a compact table mode exists and is honoured everywhere.

Dark is the default. Light is a full inversion of the same scale (every Tailwind shade swaps to its mirror), not a second palette, so hierarchy and contrast relationships survive the switch. The theme follows the operating system unless the user picks one.

**Key Characteristics:**
- Dark-first, light by scale inversion; both are first-class.
- Hairline borders (white at 10 %) instead of fills or shadows to separate regions.
- Sky is the single interface accent; violet appears only as the far end of the primary-action gradient and for system entities.
- Phase and status colours carry data meaning and are never reused as decoration.
- German copy, short labels, monospaced keys and timestamps.

## Colors

Slate neutrals do the structural work; sky is the only accent; the rainbow belongs to data.

### Primary
- **Signal Sky** (#38bdf8): focus borders, selected rows (at 10 %), links, the actor entity, checkbox/radio accents. Its text variant **Signal Sky Light** (#7dd3fc) is used for small links and chips. Rare by design: on any screen it marks what is interactive or selected.
- **Dusk Violet** (#8b5cf6): end stop of the primary gradient button (sky to violet, left to right) and the logo tile. Never used alone as UI colour.

### Neutral
- **Ledger Black** (#020617): page background and input fill (at 70 %).
- **Ledger Slate** (#0f172a): elevated surfaces: menus, dialogs, cards (login card at 60 % with blur).
- **Hairline** (white at 10 %): every border and divider; hover tint is white at 5 %.
- **Text** (#e2e8f0), **Text Strong** (#f1f5f9) for titles, **Text Muted** (#94a3b8) for controls and secondary info, **Text Faint** (#7183a0 on dark, #5c6b82 on light; both at least 4.5:1 on the page and on cards, which Tailwind's own slate-500 is not) for helper copy, placeholders and meta lines.

### Data colours (fixed, from `chronicle/models.py` and the dashboard)
- **Phase:** Identification and Exploit red (#ef4444), Analysis yellow (#eab308), Containment orange (#f97316), Mitigation green (#22c55e), Recovery emerald (#10b981), Lessons Learned blue (#3b82f6), neutral Activity slate (#94a3b8).
- **Status:** open rose (#f43f5e), in progress amber (#f59e0b, pulsing), resolved green (#22c55e), as 6px dots.
- **Entities in the graph:** actor sky, system violet (#a78bfa), event red-400, evidence slate.
- **Error:** rose text (#fda4af) on a rose 10 % fill with a rose 30 % border; destructive buttons use rose text on the normal secondary button.
- **Notices:** success uses emerald text on an emerald 10 % fill with an emerald 30 % border; warnings use amber text on an amber 10 % fill with an amber 30 to 40 % border. Same recipe as errors, only the hue changes.

### Named Rules
**The Colour Is Data Rule.** Red, yellow, orange, green, blue and rose are reserved for phases, statuses and errors. Decorative surfaces stay slate or sky.
**The One Voice Rule.** Sky is the only interface accent. A screen with two competing accents is wrong.
**The Mirror Rule.** Colours are written as theme variables (`--slate-900` etc.), never hard-coded dark values, so the light theme inverts automatically. Text on bright accent buttons stays `ink` (#020617) in both themes.

## Typography

**Body and Display Font:** Inter (with system-ui, sans-serif)
**Mono Font:** JetBrains Mono (with ui-monospace)

**Character:** A neutral grotesk for reading and a monospace for anything that is an identifier or a point in time. Inter and JetBrains Mono ship with the app (`static/vendor/fonts`, latin subset) and are embedded in reports; they are applied on the dashboard only, the other pages use the system sans (see Don'ts).

### Hierarchy
- **Title** (600, 1.25rem, 1.75rem): page headings (`h1`), always `text-strong`.
- **Heading** (600, 1.125rem): dialog and section headings.
- **Body** (400, 0.875rem, 1.25rem): the default for nearly everything, including tables, forms and menus.
- **Label** (500, 0.75rem): form labels in Text Muted, above their field.
- **Eyebrow** (600, 10px, 0.22em tracking, uppercase, Signal Sky at 80 %): the product name over auth screens.
- **Mono** (400, 0.75rem): event keys, topic keys, roles, timestamps.

### Named Rules
**The Identifier Rule.** Keys, roles and times are monospace; prose is never monospace.

## Layout

Server-rendered pages sit in a centred column (`max-w-5xl`, 24px side padding, 32px top). A single header bar (hairline underneath) holds navigation disclosure menus, search, help, theme toggle, settings and the account. It wraps on narrow screens; menus anchor to the right edge below 640px. Content is lists and tables separated by hairlines rather than cards. Spacing steps are Tailwind's 4px scale, mostly 8, 12, 16, 24. The dashboard (timeline plus graph) is embedded as a frame and uses the full width of its container. Table density is user-switchable (normal, compact trims vertical cell padding to 4px).

## Elevation & Depth

Flat at rest. Depth is conveyed by surface step (black to slate) and hairlines, not shadows. Shadows appear only on floating layers.

### Shadow Vocabulary
- **Floating** (`shadow-2xl` with black at 40 %): menus and the settings dialog; dialog backdrop is black at 60 %.
- **Glow** (`shadow-2xl` with sky at 10 %): the login card only. No other surface glows; a colour halo is not a state.
- **Glass** (backdrop blur 14px): the login card and panels that lie over content, today the legend on the dashboard's graph. Header, filter bars and detail panes of the dashboard are plain translucent slate with a hairline.

### Named Rules
**The Flat-By-Default Rule.** Lists, tables and forms carry no shadow. If it doesn't float, it doesn't cast. On the dashboard a timeline card is flat: its phase colour is the 3px left edge, and selection is a 2px ring in that colour, not a glow.

## Shapes

Soft but small. Controls and menu items are 8px (`rounded-lg`); lists, menus, dialogs and cards 12px (`rounded-xl`); the login card 16px; tiny chips and tags 6px; status dots and pills fully round. Borders are always 1px hairlines. No clipping tricks or decorative geometry.

## Components

### Buttons
- **Shape:** 8px corners, 14px text, medium padding (8px 16px).
- **Primary:** sky-to-violet horizontal gradient, ink text, semibold. Hover: `brightness(1.1)`. One per view.
- **Secondary:** transparent with a hairline border, Text colour; hover fills white at 5 %. Destructive actions use the same shape with rose text.
- **Ghost (header controls):** no border, Text Muted; hover white at 5 % and text white.

### Inputs / Fields
- **Style:** Ledger Black at 70 % fill, hairline border, 8px radius, 14px text, placeholder in Text Faint.
- **Focus:** every control, inputs included, gets the same 2px sky outline (`:focus-visible`, offset 2px, 0 on inputs); the border still shifts to sky at 60 % and forms add a 2px sky 20 % ring on top.
- **Error:** inline alert block in rose (see Colors).

### Navigation
Disclosure menus (`aria-expanded`, Escape closes and returns focus), one open at a time. Triggers are ghost buttons with a small chevron; panels are Ledger Slate, 12px radius, 4px padding, items 8px radius with white 5 % hover. The `/` key focuses search. A skip link ("Zum Inhalt springen") is the first focusable element and appears only on focus.

### Lists
Hairline-divided rows inside a 12px bordered container; row hover is white at 3 %; title in Text Strong 14px medium, meta in mono 12px Text Faint, counts right-aligned in Text Muted.

### Tables
Full width, left-aligned, 14px. The header row is a white 3 % band with 12px uppercase Text Faint labels (medium weight, wide tracking, `px-4 py-2`). Rows are separated by hairlines; compact density trims cell padding to 4px vertical.

### Dialog
Native `<dialog>`, Ledger Slate, 12px radius, hairline border, `min(28rem, 100vw - 2rem)` wide, radio groups in fieldsets with sky accent.

### Status dot and phase colour
A 6px circle for status (in progress pulses at 1.8s). Phases colour timeline markers and graph nodes using the data colours above.

## Do's and Don'ts

### Do:
- **Do** separate regions with a 1px hairline (white at 10 %) before reaching for a fill or shadow.
- **Do** write every colour through the themed variables so light and dark both work.
- **Do** use the gradient primary button once per view; everything else is secondary or ghost.
- **Do** set keys, roles and timestamps in JetBrains Mono at 12px.
- **Do** keep German labels short and let the table density setting apply to any new table.
- **Do** keep phase and status colours identical across list, timeline, graph and report.

### Don't:
- **Don't** use red, yellow, orange, green or blue for anything but phase, status or error meaning.
- **Don't** add a second interface accent next to sky.
- **Don't** put shadows on lists, tables or in-page cards.
- **Don't** add colour halos or glows to cards, buttons, dots or logos. Phase colour is an edge or a ring, never a glow.
- **Don't** hard-code dark-theme hex values in templates; the dashboard stylesheet still does in places and should be migrated.
- **Don't** add new external font or script requests; reports must keep opening without the network (ADR 0006). The dashboard uses Inter while the other pages fall back to the system sans; decide deliberately before extending either.
- **Don't** remove or replace the focus outline; a colour change alone is not a focus indicator.
