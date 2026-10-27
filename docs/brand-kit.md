# LiveX brand kit (for the calendar chatbot restyle)

Source: https://www.livex.ai (redirects to livex.ai). Read 2026-10-27 from the live HTML, its 7 Next.js stylesheets (`/_next/static/immutable/chunks/*.css`), the homepage, /digital-ai and /about, plus two screenshots of the homepage.

The look in one line: white page, near-black type, one deep blue for actions, a brighter blue for highlights, pill buttons, almost no shadows, small dot-matrix labels.

## Colors

| Role | Hex | Where the site uses it | Source |
|---|---|---|---|
| Primary (action blue) | `#050DC8` | `--livex-button-circle-blue`: Book a Demo outline, its hover fill, arrow icons, carousel buttons, card panels | from CSS |
| Accent (bright blue) | `#1F8FFF` | `--livex-accent-blue`: highlighted words in body copy, kicker scan effect, gradient end | from CSS |
| Accent gradient | `#050DC8 → #1F8FFF`, 90deg | `introGradientSweepText`, the blue phrase in "LiveX is a *Physical AI…*" | from CSS |
| Diagram blue | `#034CFF` | Strokes and dots in the business diagrams only | from CSS |
| Ink | `#050505` | Main text, kickers, borders at low alpha | from CSS |
| Navy | `#010720` | Footer, case-studies hero, cookie card heading | from CSS |
| Background | `#FFFFFF` | `--background`, page body | from CSS |
| Surface | `#F7F7F7` | Proof/impact cards, timeline band, control buttons | from CSS |
| Hairline | `#E5E5E5` | 1px borders on tags, meta lists, dividers | from CSS |
| Muted label | `#787878`, `#B4B4B4` | Secondary kickers | from CSS |
| Translucent ink | `#0505052E`, `#0505053D` | Inactive text, dim states | from CSS |

`--foreground` is set to `#0F172A`, but most components override it with `#050505`. Use `#050505`.

## Typography

- **PP Neue Montreal** (Pangram Pangram, commercial): Book 400, Medium 500, Bold 700, self-hosted .otf. Used for nearly all text. CSS stack: `"ppNeueMontreal", Arial, Helvetica, sans-serif`. Free fallback: **Inter** or **Geist** at the same weights.
- **Dot Matrix** (custom .ttf, unnamed foundry): only for small uppercase eyebrows like `>> WHAT IS LIVEX` (12–13px desktop, 8–10px mobile). Free fallback: **DotGothic16** (Google Fonts), or `ui-monospace` uppercase.
- **General Sans** (variable, Fontshare, free) is loaded for a couple of elements. Minor.
- Weights: 400 does most of the work (213 uses vs 69 for 500). Headlines are 400, not bold.
- Scale: hero/section H2 `clamp(46px, 4.2vw, 61px)`, line-height ~0.97, letter-spacing 0. Intro body 38–48px at line-height 1.05. Card titles 18–20px. Body 16px (line-height 1.16 on mobile). Labels 12–15px.

## Shape and depth

- Buttons and chips are full pills: `border-radius: 300px` (85 uses) or `999px`. Icon buttons are circles (`50%`).
- Cards and media: 8px most common, then 10, 12, 16px.
- Borders: 1–1.2px. CTAs draw the outline as an inset shadow: `inset 0 0 0 1.2px #050DC8`.
- Shadows: `none` in most places. The one soft card shadow is `0 18px 45px #0505052E`, and a focus ring is `0 0 0 6px #0426A129, 0 10px 24px #0426A138`.
- Spacing is generous. Section gap is `clamp(96px, 8vw, 115px)` and page gutter is `clamp(40px, 5.5vw, 80px)` (21–24px on mobile).
- Signature CTA: white pill with a blue outline, blue label and a blue circular arrow icon at the left. On hover the pill fills `#050DC8`, the text turns white and the icon slides to the right and turns white with a blue arrow.

## Logo

- Wordmark (X mark + "LIVEX.AI"): `https://cdn.livex.ai/brand/livex-wordmark.svg`. 117×31 viewBox. The site applies it as a CSS mask at 101px wide, so it takes the text color: white on the hero photo, ink on white.
- Mark only: `https://www.livex.ai/icon.svg`. Black, white in dark mode.
- Use: show the logo from its URL and recolor only through a mask (ink or white). Don't redraw, stretch or recolor it blue, and don't use it as the bot avatar. The assets are not ours to redistribute.

## Voice

- Short declarative headlines with a period, often in pairs: "Beyond Screens. Into Life.", "Six Agents. One Intelligence Layer."
- Leads with outcomes and numbers: 90% self-resolution, 3X conversions, "under a month".
- Calls itself "human-like", "physical AI" and "real-world". The tone is confident and enterprise-facing, without jokes.
- Short contrast lines: "Deploying AI is easy. Creating meaningful customer experiences at scale is not."
- Title Case headings. Eyebrows are uppercase and start with `>>`.

## Apply to a minimal calendar chatbot

1. Background `#FFFFFF`, text `#050505`, and `#F7F7F7` for the calendar grid surface and assistant message bubbles. User bubbles are `#050DC8` with white text.
2. Primary buttons (Book, Confirm) use the LiveX pill: radius 999px, 1.2px `#050DC8` outline, blue label, fill on hover. Only one filled blue element per view.
3. Event blocks: light tint of `#1F8FFF` (about 12% alpha) with a 3px `#050DC8` left bar, ink text, radius 8px. The selected event fills solid `#050DC8`.
4. Today: a `#050DC8` filled circle behind the date number, white numeral. Don't shade the whole day column.
5. Section labels ("TODAY", "UPCOMING", "ASSISTANT") use the dot-matrix eyebrow style: 12px uppercase, `>>` prefix, `#787878`.
6. Type: Inter/Geist (or PP Neue Montreal if licensed) at 400. Use 500 only on times and button labels. Day header ~28px with tight line-height; everything else 14–16px.
7. Depth: `#E5E5E5` 1px hairlines for grid lines and the input border. No shadows apart from the focus ring.
8. Keep it minimal. Leave out the hero photography, hologram imagery, gradient sweeps, scan and scroll animations, and the navy footer. Use `#1F8FFF` only as a highlight (a link or a tint), never for body text or buttons.

## Not verified

- No logged-in or product UI was checked. The kit is based on the marketing site only.
- Hex usage counts come from compiled CSS, so some colors may belong to sections that aren't currently shown.
- The license status of the Dot Matrix font is unknown. PP Neue Montreal needs a paid license.
- The event-tint, today-circle and bubble rules are my mapping of the site's style onto a calendar. LiveX does not have its own calendar UI.
