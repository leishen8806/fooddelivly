---
name: Tea Cafe Interface System
description: Shared visual and interaction standards for the Tea Cafe Telegram Mini App and browser admin console.
type: design-system
colors:
  brand: "#4C6971"
  accent: "#46AAD3"
  canvas: "#F4F8F9"
  surface: "#FBFCFC"
  raised_surface: "#FFFFFF"
  tint: "#E2F1F5"
  text: "#25383D"
  muted: "#71858A"
  border: "#DCE5E8"
  danger: "#A33B30"
  danger_surface: "#F8E9E7"
typography:
  font_stack: 'Inter, "Noto Sans Khmer", "Khmer OS System", "Noto Sans SC", "PingFang SC", "Microsoft YaHei", system-ui, "Segoe UI", sans-serif'
  scale: "12/18, 14/20, 16/24, 20/28, 24/32, 28/36, 36/44 px"
  weights: "400 regular, 500 medium, 600 semibold, 700 bold, 800 heavy"
spacing:
  base: 4
  scale: "4, 8, 12, 16, 20, 24, 32, 40, 48 px"
rounded:
  control: 10
  card: 16
  feature: 20
  pill: 999
components:
  control_height: 44
  primary_control_height: 48
  focus_ring: "0 0 0 3px #46AAD347"
  card_shadow: "0 8px 28px #4C697114"
---

# Tea Cafe Interface System

## Overview

One visual system serves the Telegram Mini App, admin console, and admin login at `food.workline.ink`. The Mini App uses a calm, readable rhythm for browsing and checkout. The admin uses the same type, control, and spacing tokens with denser content layout. Brand colors and the current Tea Cafe screen structure remain the visual baseline.

The canonical runtime tokens and component rules live in `frontend/src/index.css`. This document records the design contract; keep it aligned when changing the CSS. Behavioral requirements and screen flows remain in `docs/ui/UI-DESIGN-HANDOFF.md`.

## Colors

- Brand slate: `#4C6971`; primary action aqua: `#46AAD3`.
- Page canvas: `#F4F8F9`; card surface: `#FBFCFC`; raised input surface: `#FFFFFF`; selected/tinted surface: `#E2F1F5`.
- Main text: `#25383D`; secondary text: `#71858A`; divider: `#DCE5E8`.
- Errors use `#A33B30` on `#F8E9E7`. Do not use the payment-pending tint as proof that money was received.

## Typography

- Use the shared system stack declared in the CSS. Do not require a remote font download for first render.
- Use a 4px-based scale: caption 12/18, small 14/20, body 16/24, section title 20/28, title 24/32, page title 28/36, display 36/44 (font size / line height, px).
- Use weights 400, 500, 600, 700, and 800 for regular through heavy emphasis. Reserve heavy weight for short labels and key values.
- Khmer uses the Khmer font fallbacks with a 1.7 line height so stacked marks remain legible. Simplified Chinese prefers its system CJK fallback.
- Body copy stays at 16/24. Supporting text can use 14/20; metadata can use 12/18. Avoid reducing action labels or dense table text below 12px.
- Use tabular numerals for money, counts, and identifiers when alignment matters.

## Layout

- Spacing follows 4, 8, 12, 16, 20, 24, 32, 40, and 48px. Prefer these tokens over one-off values.
- Mini App content is centered and capped at 1100px. Keep comfortable 16px mobile page gutters and clear separation between menu, cart, and payment stages.
- Admin navigation uses a 224px desktop rail and collapses to a compact mobile navigation. Main content is capped at 1500px and keeps enough room for table scrolling.
- On narrow screens, reflow cards and metrics before shrinking text or controls. Preserve touch targets at 44px or larger.

## Elevation & Depth

- Cards use a subtle border and one soft shadow: `0 8px 28px rgb(76 105 113 / 8%)`.
- Use the brand color for the hero surface and the aqua color for primary actions. Avoid adding extra gradients, dark shadows, or new accent colors.
- Use a visible 3px aqua focus halo for keyboard and switch navigation.

## Shapes

- Inputs, selects, and compact image frames use 10px corners.
- Cards use 16px corners; feature surfaces use 20px; pill controls use fully rounded ends.
- Standard controls are 44px high; primary actions are 48px high. Checkboxes are the only compact native control exception.

## Components

- Buttons share a 14/20 semibold label, 44px minimum height, and the common focus state. Primary buttons use the aqua fill, white text, and 48px height.
- Inputs and selects share a 44px height, 14/20 text, 10px corners, and the same border/focus behavior.
- Labels use 14/20 semibold; helper text uses 12/18. Keep a consistent 8px label-to-control gap.
- Cards and panels share surface, border, 16px radius, and subtle shadow. Tables use 14/20 body rows and 12/18 headers.
- Keep the same hierarchy across app and admin; make the admin denser through layout and whitespace, not smaller typography or controls.

## Do’s and Don’ts

- Do use CSS custom properties from `frontend/src/index.css` for new UI values.
- Do verify English, Chinese, and Khmer wrapping at mobile widths when changing typography or component sizing.
- Do keep status meaning explicit: a submitted payment image is pending review, and only staff confirmation represents confirmed payment.
- Don’t add local one-off font sizes, control heights, radii, or spacing without a documented need.
- Don’t clip Khmer text, shrink touch controls below 44px, or use color alone to communicate a status.
