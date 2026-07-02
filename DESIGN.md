---
name: Vital Clarity
colors:
  surface: '#f6fafb'
  surface-dim: '#d6dbdc'
  surface-bright: '#f6fafb'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f0f4f5'
  surface-container: '#eaeff0'
  surface-container-high: '#e5e9ea'
  surface-container-highest: '#dfe3e4'
  on-surface: '#181c1d'
  on-surface-variant: '#3e4850'
  inverse-surface: '#2c3132'
  inverse-on-surface: '#edf1f2'
  outline: '#6e7881'
  outline-variant: '#bdc8d2'
  surface-tint: '#00658f'
  primary: '#00658f'
  on-primary: '#ffffff'
  primary-container: '#00b0f6'
  on-primary-container: '#003f5c'
  inverse-primary: '#86cfff'
  secondary: '#416375'
  on-secondary: '#ffffff'
  secondary-container: '#c4e7fd'
  on-secondary-container: '#47697b'
  tertiary: '#815600'
  on-tertiary: '#ffffff'
  tertiary-container: '#de9700'
  on-tertiary-container: '#523500'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#c7e7ff'
  primary-fixed-dim: '#86cfff'
  on-primary-fixed: '#001e2e'
  on-primary-fixed-variant: '#004c6d'
  secondary-fixed: '#c4e7fd'
  secondary-fixed-dim: '#a9cbe0'
  on-secondary-fixed: '#001e2b'
  on-secondary-fixed-variant: '#294b5c'
  tertiary-fixed: '#ffddb1'
  tertiary-fixed-dim: '#ffba4a'
  on-tertiary-fixed: '#291800'
  on-tertiary-fixed-variant: '#624000'
  background: '#f6fafb'
  on-background: '#181c1d'
  surface-variant: '#dfe3e4'
  white: '#FFFFFF'
  border-subtle: '#E2E8F0'
  text-main: '#002939'
  text-muted: '#546E7A'
typography:
  display-lg:
    fontFamily: Rubik
    fontSize: 48px
    fontWeight: '500'
    lineHeight: 56px
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Rubik
    fontSize: 32px
    fontWeight: '500'
    lineHeight: 40px
  headline-lg-mobile:
    fontFamily: Rubik
    fontSize: 24px
    fontWeight: '500'
    lineHeight: 32px
  headline-md:
    fontFamily: Rubik
    fontSize: 24px
    fontWeight: '500'
    lineHeight: 32px
  body-lg:
    fontFamily: Hanken Grotesk
    fontSize: 18px
    fontWeight: '400'
    lineHeight: 28px
  body-md:
    fontFamily: Hanken Grotesk
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  label-md:
    fontFamily: Hanken Grotesk
    fontSize: 14px
    fontWeight: '600'
    lineHeight: 20px
    letterSpacing: 0.01em
  label-sm:
    fontFamily: Hanken Grotesk
    fontSize: 12px
    fontWeight: '500'
    lineHeight: 16px
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  base: 8px
  container-max: 1200px
  gutter: 24px
  margin-mobile: 16px
  margin-desktop: 48px
  stack-sm: 12px
  stack-md: 24px
  stack-lg: 48px
---

## Brand & Style
The design system is anchored in the concept of "Reassuring Precision." For a health insurance provider, the interface must bridge the gap between clinical efficiency and human empathy. The aesthetic direction is **Corporate / Modern** with a strong infusion of **Minimalism**. 

The brand personality is professional, transparent, and calm. It avoids "tech-bro" trends in favor of a timeless, high-trust environment. Design decisions prioritize accessibility and clarity, using generous whitespace to reduce cognitive load during potentially stressful user journeys (like claims or policy renewals). The visual narrative moves away from generic digital artifacts by utilizing purposeful alignment, refined line weights, and a photographic style that emphasizes real people in natural light.

## Colors
The palette is dominated by "Trust Blue" (`#00B0F6`), used strategically for primary actions and brand identifiers. The deep navy (`#002939`) provides grounding and is used for primary typography to ensure high legibility and a sense of authority. 

`#FFAF14` is reserved strictly for functional accents—such as notifications, warnings, or highlighting "New" features—to ensure it doesn't overwhelm the serene medical blue. The background utilizes a mix of pure white for primary content areas and a very soft cool grey (`#F3F7F8`) to define section boundaries without the harshness of high-contrast borders.

## Typography
This design system pairs **Rubik** for headings with **Hanken Grotesk** for body and UI elements. Rubik’s slightly rounded corners add the "human touch," making headers feel approachable rather than sterile. Hanken Grotesk provides a clean, systematic structure for long-form reading and data-heavy tables, ensuring maximum legibility.

Type scales are generous. Line heights are kept airy (1.5x for body) to assist users who may be scanning for critical health information. We use weight rather than color to create hierarchy, keeping the primary text color almost consistently in the deep navy palette for maximum contrast.

## Layout & Spacing
The layout follows a **Fixed Grid** model on desktop (1200px max-width) to maintain control over line lengths and reading comfort. It utilizes a 12-column structure with 24px gutters.

The spacing rhythm is strictly based on 8px increments. We favor "extra-loose" vertical stacking (`stack-lg`) between major sections to emphasize the premium, uncluttered nature of the service. On mobile, margins shrink to 16px, and the 12-column grid collapses into a single-column flow, with horizontal padding within cards reduced to maximize screen real estate.

## Elevation & Depth
Depth is conveyed through **Tonal Layers** and **Ambient Shadows**. We avoid heavy dropshadows. Instead, we use "soft-light" shadows: long-range, low-opacity (4-8%) blurs with a slight blue tint (`#002939`) to make elements feel like they are gently lifting off the surface.

Interactive surfaces like cards and primary buttons use these shadows to indicate clickability. Passive containers use **Low-contrast outlines** (1px solid `#E2E8F0`) to provide structure without adding visual noise. This combination ensures the UI feels tactile but remains light and breathable.

## Shapes
The shape language is **Rounded** (0.5rem / 8px). This radius is large enough to feel friendly and modern, but small enough to maintain a professional, organized structure. 

- **Standard Elements:** 8px radius (Buttons, Input fields, Small cards).
- **Large Containers:** 16px radius (Policy benefit cards, Modal windows).
- **Interactive Pills:** Full radius (Status tags, Filter chips) to distinguish them from structural elements.

## Components
- **Buttons:** Primary buttons use the brand blue with white text and a soft lift shadow on hover. Secondary buttons use a ghost style with a navy border.
- **Input Fields:** Large 48px height with 1px soft-grey borders. On focus, the border transitions to brand blue with a 3px soft-glow (outer shadow).
- **Cards:** Used extensively for plan details. They feature a white background, 16px internal padding, and a subtle 1px border. 
- **Chips & Tags:** Small, pill-shaped elements with low-saturation background tints (e.g., a 10% opacity blue background for a "Active" status).
- **Policy Progress Bar:** A custom component using a rounded track in light grey and a primary blue fill to show completion of a claim or application.
- **Iconography:** Use "Linear" icons with a 1.5px stroke weight. Icons should be dual-toned, using the primary blue for accents and navy for the main structure.