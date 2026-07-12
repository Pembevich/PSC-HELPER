---
version: "0.8.1"
name: P.OS Public Site
description: A precise graphite product showcase for the P.OS Discord operating system, with restrained motion, signal-red interaction, authored gold identity details, and factual interface demonstrations.
colors:
  command-black: "#07080A"
  graphite: "#0D0F13"
  steel: "#171A20"
  silver-canvas: "#EDF0F4"
  paper: "#F8F9FB"
  primary-dark: "#F2F3F5"
  primary-light: "#111318"
  primary: "#F34D52"
  signal-red: "#F34D52"
  logo-gold: "#D9C51D"
  verified-green: "#67C77A"
  trace-cyan: "#43C8D9"
typography:
  display:
    fontFamily: Oxanium, sans-serif
    fontWeight: 620
    lineHeight: 1.03
    letterSpacing: 0
  body:
    fontFamily: Manrope, sans-serif
    fontWeight: 400
    lineHeight: 1.65
    letterSpacing: 0
  data:
    fontFamily: SFMono-Regular, Consolas, monospace
    fontWeight: 500
    lineHeight: 1.5
    letterSpacing: 0
rounded:
  control: 4px
  button: 6px
  surface: 8px
spacing:
  xs: 4px
  sm: 8px
  md: 12px
  lg: 18px
  xl: 28px
  xxl: 48px
modes:
  dark:
    background: "{colors.command-black}"
    surface: "{colors.graphite}"
    text-primary: "{colors.primary-dark}"
    accent: "{colors.signal-red}"
  light:
    background: "{colors.silver-canvas}"
    surface: "{colors.paper}"
    text-primary: "{colors.primary-light}"
    accent: "{colors.signal-red}"
---

# P.OS public site design system

## Product role

The site presents P.OS as an AI operating layer for Discord: conversational
assistant, server manager, administrator, security system, factual memory, and
media utility. It is a public product showcase, not an administration console
and not internal documentation.

Public copy may describe verified capabilities visible in the repository. It
must never expose secrets, Discord IDs, model providers, system prompts,
detector thresholds, private channel names, environment variables, or exact
security-routing logic. Illustrative interface data is visibly marked as a demo
and must not look like real telemetry.

## Atmosphere

- Strategic-interface, precise, calm, slightly dangerous.
- Density: 6/10. Hero and operator surfaces may be cockpit-dense; prose scenes
  stay spacious.
- Variance: 7/10. Alternate open typographic scenes, real tool surfaces,
  timelines, and one ecosystem diagram. Never repeat the same section layout.
- Motion: 6/10. Weighty and restrained. Motion explains state, focus, and data
  flow; it is not ambient decoration.
- The accepted visual anchor is the P.OS concept: a graphite first viewport,
  oversized P.OS identity, red signal point, authored logo geometry, and a
  tilted command console with a silver section visible below.

## Color roles

### Dark theme

- Command black `#07080A`: page canvas and hero.
- Graphite `#0D0F13`: tools and elevated work surfaces.
- Steel `#171A20`: selected and nested tool surfaces.
- Primary ink `#F2F3F5`: headings and important controls.
- Secondary ink `#A2A8B3`: body copy and tool labels.
- Muted ink `#676E79`: metadata only.
- Structural line `rgba(255, 255, 255, 0.12)`: boundaries and separators.

### Light theme

- Silver canvas `#EDF0F4`: page canvas.
- Paper surface `#F8F9FB`: open content areas.
- White tool `#FFFFFF`: interactive tools.
- Primary ink `#111318`: headings and important controls.
- Secondary ink `#555D69`: body copy and tool labels.
- Muted ink `#7A828E`: metadata only.
- Structural line `rgba(17, 19, 24, 0.12)`: boundaries and separators.

### Brand and semantic color

- Signal red `#F34D52`: primary actions, current state, focus, and the P.OS orb.
- Logo gold `#D9C51D`: reserved for the authored logo and rare identity details.
- Verified green `#67C77A`: successful or protected state only.
- Trace cyan `#43C8D9`: neutral recorded event only.

Red is the interaction accent across the whole page. Gold, green, and cyan are
not competing call-to-action colors.

## Typography

- Display: Oxanium variable, weights 520-760. Used for P.OS identity and major
  scene headlines only.
- Body: Manrope variable, weights 400-720. Used for all public prose and controls.
- Data: system monospace. Used for events, timestamps, commands, and status.
- Letter spacing is always `0`.
- Desktop hero identity: 176px; supporting name: 30px. Mobile identity: 82px;
  supporting name: 22px.
- Section titles: 56px desktop, 36px tablet, 30px mobile.
- Tool titles: 18-24px. Body: 16-18px with a maximum line length of 62ch.
- Every heading uses balanced wrapping; body copy uses pretty wrapping.

## Layout

- Maximum content width: 1440px.
- Desktop outer gutter: 48px. Tablet: 28px. Mobile: 18px.
- Header: one line up to 1024px, then a compact menu. Maximum desktop height 72px.
- Hero fits in the initial viewport and leaves the beginning of the next band
  visible at common desktop heights.
- No nested cards. A card exists only when it represents a real tool, repeated
  event, or selectable mode.
- Tool and card radius: 8px maximum. Buttons: 6px. Small icon controls: 4px.
- Each major scene uses a different container model: split hero, capability rail,
  conversational workbench, open ecosystem diagram, security layers, operator
  timeline, evidence search, and a unified function index.

## Components

- Header: compact wordmark, four section links, language segmented control,
  theme segmented control, and mobile menu button.
- Buttons: red primary or structural ghost. Minimum 44px hit target; one-line
  labels; 0.98 active scale; clear focus outline.
- Console: real selectable modes with accessible tab semantics. It is labelled
  as an interface preview and contains no fake production metrics.
- Capability rail: open horizontal list separated by lines, never floating cards;
  forms and staff workflows belong to the public capability set.
- Dialogue workbench: selectable example prompts and one response surface.
- Ecosystem: central P.OS identity with connected capability nodes; no orbiting
  decorative blobs.
- Security layers: deterministic checks, AI review, and verified authority shown
  as a clear sequence without revealing implementation thresholds.
- Evidence search: query, matched events, and deletion state. No invented server
  count, uptime percentage, or customer metrics.
- Function index: one consolidated surface for dialogue, moderation, server
  control, memory, forms, and media. `p.gif` appears only as the media utility,
  never as a standalone product pillar.

## Motion and cursor

- Use only `transform`, `opacity`, and color for frequent animation.
- Standard timing: 180ms controls, 500-760ms scene reveals.
- Cursor flow is desktop-only, decorative, `pointer-events: none`, and replaces
  the system cursor only after pointer movement is detected.
- Console tilt is limited to a few degrees and resets immediately on exit.
- Status traces may pulse softly; large perpetual rotations and bouncing scroll
  indicators are prohibited.
- `prefers-reduced-motion: reduce` disables cursor, tilt, stagger, and looping
  motion while preserving all information and controls.

## Responsive behavior

- No horizontal overflow at 320px or wider.
- All touch targets are at least 44px.
- Hero becomes copy then console below 1100px.
- The console side rail becomes a horizontal tab strip on mobile; nonessential
  metadata is removed before content is compressed.
- Capability rail and evidence rows become vertical lists with dividers.
- The ecosystem becomes a linear flow below 768px instead of shrinking labels.
- Long Russian words must wrap without clipping controls or preceding content.

## Banned patterns

- No purple or blue AI gradients, bokeh, gradient orbs, or decorative blobs.
- No fake metrics, testimonials, server counts, customer logos, or activity data
  presented as factual.
- No public system prompt, owner ID, API/provider details, security thresholds,
  private role/channel names, or secret configuration.
- No repeated three-card feature grids, testimonial carousels, pricing towers,
  FAQ accordions, pill badges, or filler eyebrows above every heading.
- No generic AI copy: “seamless”, “next-gen”, “unleash”, “revolutionary”, or
  equivalent Russian cliches.
- No oversized section typography inside compact tools.
- No dead links, inert buttons, hidden focus state, or controls that only look
  interactive.
- No animation that changes layout dimensions or blocks scrolling.
