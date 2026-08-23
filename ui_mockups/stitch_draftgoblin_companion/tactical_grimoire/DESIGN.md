---
name: Tactical Grimoire
colors:
  surface: '#131313'
  surface-dim: '#131313'
  surface-bright: '#393939'
  surface-container-lowest: '#0e0e0e'
  surface-container-low: '#1c1b1b'
  surface-container: '#201f1f'
  surface-container-high: '#2a2a2a'
  surface-container-highest: '#353534'
  on-surface: '#e5e2e1'
  on-surface-variant: '#c4c9b0'
  inverse-surface: '#e5e2e1'
  inverse-on-surface: '#313030'
  outline: '#8e937c'
  outline-variant: '#444936'
  surface-tint: '#aad630'
  primary: '#aad630'
  on-primary: '#273500'
  primary-container: '#8db600'
  on-primary-container: '#324300'
  inverse-primary: '#4e6700'
  secondary: '#ffb693'
  on-secondary: '#562000'
  secondary-container: '#ea6b1e'
  on-secondary-container: '#4b1b00'
  tertiary: '#ffa9fa'
  on-tertiary: '#5a005f'
  tertiary-container: '#f07cf0'
  on-tertiary-container: '#6f0076'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#c6f34c'
  primary-fixed-dim: '#aad630'
  on-primary-fixed: '#151f00'
  on-primary-fixed-variant: '#3a4d00'
  secondary-fixed: '#ffdbcc'
  secondary-fixed-dim: '#ffb693'
  on-secondary-fixed: '#351000'
  on-secondary-fixed-variant: '#7a3000'
  tertiary-fixed: '#ffd6f8'
  tertiary-fixed-dim: '#ffa9fa'
  on-tertiary-fixed: '#37003b'
  on-tertiary-fixed-variant: '#7d0885'
  background: '#131313'
  on-background: '#e5e2e1'
  surface-variant: '#353534'
  surface-alt: '#1A1A1B'
  text-emphasis: '#F5F5DC'
  mana-white: '#F9FAFB'
  mana-blue: '#0EA5E9'
  mana-black: '#4B5563'
  mana-red: '#EF4444'
  mana-green: '#22C55E'
typography:
  display:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '700'
    lineHeight: 32px
    letterSpacing: -0.02em
  headline:
    fontFamily: Inter
    fontSize: 18px
    fontWeight: '600'
    lineHeight: 24px
  body:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  body-sm:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 16px
  data-lg:
    fontFamily: JetBrains Mono
    fontSize: 16px
    fontWeight: '600'
    lineHeight: 20px
  data-md:
    fontFamily: JetBrains Mono
    fontSize: 13px
    fontWeight: '500'
    lineHeight: 16px
  label-caps:
    fontFamily: Inter
    fontSize: 11px
    fontWeight: '700'
    lineHeight: 16px
    letterSpacing: 0.05em
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  unit: 4px
  gutter: 12px
  margin-desktop: 24px
  card-padding: 12px
---

## Brand & Style
The design system is a high-fidelity utility for Magic: The Gathering Arena, balancing the "Modern Cartographer" concept with "Tactile Fantasy." The aesthetic rejects typical "gamer" tropes—neon glows and heavy ornaments—in favor of a professional, tool-like character reminiscent of precision instruments and vintage field guides.

The style is **Modern Tactile**. It utilizes subtle tonal layering, crisp micro-interactions, and high-density information layouts to help players make data-driven decisions during high-pressure drafts. The emotional response should be one of "clever mastery"—the user feels like a seasoned explorer equipped with a sophisticated tracking device rather than a casual gamer.

## Colors
This design system utilizes a deep, low-glare dark palette to reduce eye strain during long sessions.

- **Primary (Goblin Green):** Reserved for "The Recommendation"—the optimal choice, successful drafts, and positive trends.
- **Secondary (Burnt Orange):** Used for progress bars, warnings, and cautionary data points.
- **Backgrounds:** A tiered system of Charcoal (`#121212`) for the base and Blue-Black (`#1A1A1B`) for elevated cards and containers.
- **Typography:** Warm Ivory (`#F5F5DC`) provides high-contrast legibility without the harshness of pure white, evoking the feel of aged parchment or technical manuals.
- **Mana Palette:** These are functional semantic colors strictly mapped to Magic: The Gathering's identity colors.

## Typography
The typography system prioritizes data density and legibility.

- **Inter** handles all narrative, UI navigation, and card descriptions. It provides a neutral, modern bridge to the technical nature of the app.
- **JetBrains Mono** is used exclusively for numbers, win rates, pick orders, and mana costs. The monospaced nature ensures that columns of numbers align perfectly in tables and card overlays, reinforcing the "instrument" aesthetic.
- **Label Caps** should be used for category headers and table headers to provide clear structural hierarchy.

## Layout & Spacing
This is a high-density desktop application. It uses a **Fixed Grid** model with a sidebar for navigation and a multi-pane main content area.

- **Base Unit:** A 4px grid ensures tight, technical alignment.
- **Density:** Padding should be economical. Information is the priority.
- **Panels:** Use a 12-column system within the main content area. Data tables should use condensed row heights (32px or 36px) to maximize the amount of information visible without scrolling.
- **Responsive:** On narrower desktop windows, the secondary "Stats" panel should collapse into a drawer or shift beneath the main "Card Selection" view.

## Elevation & Depth
The design system avoids heavy drop shadows in favor of **Tonal Layers** and **Crisp Outlines**.

- **Level 0 (Background):** `#121212` for the main application shell.
- **Level 1 (Containers):** `#1A1A1B` for cards, sidebars, and panels.
- **Borders:** Instead of shadows, use 1px solid borders (`#2A2A2B`) to define edges.
- **Active State:** Highlight active or hovered cards with a subtle primary color (`#8DB600`) inner-glow or a weighted border.
- **Depth:** Occasional use of "Inscribed" depth for input fields—a 1px darker top-inner shadow to make them feel carved into the UI.

## Shapes
Shapes are **Soft** but leaning toward sharp. The 4px (0.25rem) standard radius conveys a sense of precision manufacturing.

- **Cards/Panels:** 4px radius.
- **Buttons/Inputs:** 4px radius.
- **Draft Status Indicators:** Circular (pill) shapes for status pips or mana symbols.
- **Avoid:** Large, bubbly radii or fully square "brutalist" corners. The goal is "refined tool," not "toy."

## Components
- **Buttons:** Primary buttons use Goblin Green with black text. Secondary buttons use an outline style with Warm Ivory text. Buttons have a "pressed" state that shifts 1px downward to simulate tactile feedback.
- **Card Overlays:** Small chips indicating "Pick Quality" should be positioned at the top-right of card images, using Primary (Good), Secondary (Warning), or Neutral (Middling) colors.
- **Data Tables:** Use alternating row stripes (Zebra striping) with a very subtle variance between `#1A1A1B` and `#1E1E1F`. Headers are `label-caps` in Warm Ivory.
- **Progress Bars:** Use Burnt Orange for the "filled" portion to indicate progress or completion toward a deck-building goal.
- **Inputs:** Dark backgrounds with a 1px border. Focus state should change the border color to Goblin Green.
- **Mana Pips:** Circular icons with the specific Mana Colors, housing the JetBrains Mono number or symbol.
