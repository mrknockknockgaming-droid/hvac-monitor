# Fullscope

Fullscope is the interface of a residential HVAC monitoring and diagnostic system for contractors and service technicians. ESP32 nodes on the refrigerant and air sides of a split system report over MQTT; the software calculates saturation temperatures, superheat, subcooling, delta-T, static pressure and capacity, and interprets them. It should read like a professional service instrument: calm, dense, exact. It is a tool, not a marketing site.

## Brand

- **Name**: Fullscope. **Tagline**: *Continuous diagnostics & control* — written in sentence case in running text; the logo itself sets it in capitals.
- **Logo**: chrome wordmark with a green reticle. Files in the Logos group: the full lockup with tagline and the wordmark alone, each as the original on black and as a transparent PNG. The artwork is light chrome and glow, so the transparent version only works on dark grounds — never place it on white or light grey; use the black original there.
- **In the UI** the transparent wordmark sits at the left of the header bar, which is dark (`bar-*` tokens) in both themes so the logo always has its ground. The tagline is set as text in the logo's style — tracked capitals (`.caps`, 0.18em) — the one place the UI uses all caps. Below about 120px wide the tagline in the lockup is unreadable, so use the wordmark and set the tagline as text beside it.
- **Brand colours** (`brand-black`, `brand-chrome`, `brand-green`) belong to the logo and brand surfaces only. The UI never uses the reticle green — green in the UI means *ok*, and a neon brand green next to state colours would blur that.

The **Monitor** screen (component `Dashboard`) is the reference layout. Everything else in this system is a piece of it.

## The four questions

The Monitor screen is ordered so a technician can answer, top to bottom:

1. **What is the system doing right now?** — status bar: mode and thermostat calls (Y G O W), node links, last update, outdoor air, runtime.
2. **Are refrigerant conditions normal?** — summary strip (superheat, subcooling) and the unified refrigerant table.
3. **Is the air side performing?** — delta-T, TESP, airflow, capacity; the air-side table with static pressure.
4. **Is anything wrong?** — diagnostics counts in the status bar, the diagnostics table with evidence.

## Raw → calculated → interpretation

This is the product's core rule. Never hide a measurement behind a score.

| Layer | Where it appears | Marker |
|---|---|---|
| Raw measurement | Tables, trend, sensor health | `MEAS` tag (solid outline) |
| Calculated value | Tables, summary strip, trend "Calculated" lane | `CALC` tag (dashed outline) |
| Configured value | Airflow, targets, limits | `CFG` tag |
| Interpretation | Only status words (Normal / Above target / High) and the diagnostics table | state glyph + word |

Diagnostics always show their evidence and use hedged language: "Possible airflow restriction", never "Airflow is restricted". Each row expands to *what triggered it*, *current vs expected*, *how long*, and *possible causes ranked by evidence*.

## Voice

- Sentence case everywhere; no all-caps headings. Short tags (`MEAS`, `CALC`, `Y`, `O`) and the tagline are the only uppercase.
- Units always, and always visually secondary: `128.4 psig`, `12.4 °F`, `0.72 in. w.c.`, `1,400 CFM`, `34,900 BTU/hr`. Negative static uses a true minus (−0.28); positive static carries a plus (+0.44).
- Don't imply precision you don't have: capacity rounds to 100 BTU/hr and states its airflow source.
- Missing optional sensors say "Not installed", in `ink-faint` — never a fault. Only a sensor configured as required can raise a fault.

## Visual foundations

- **Dark first**, charcoal not black (`bg`, `surface`, `surface-2`, `surface-3`, `inset` for chart wells). A light theme exists for bright rooms and printing.
- **Structure from lines, not cards.** Hairlines (`line`) divide rows and cells; panels have a 1px outline and `radius-2` (3px) at most. No shadows, gradients, glows or glass.
- **Colour means state.** `ok`, `caution`, `fault`, `advisory`, `sensor`, `offline`. Every state is also a glyph shape (● ▲ ■ ◆ ✕ ○) plus a word, so it survives colour blindness and greyscale printing. A non-normal summary cell gets a 2px top rule in its state colour; a warning cell also gets `caution-soft` behind it.
- **Low vs high side** are told apart by a 2px rule under the column header (`side-low` steel, `side-high` copper) and by line colour in the trend — never by filling whole areas blue and red.
- **Type**: everything is set in DIN, the face of the logo's tagline — D-DIN (open licence, DIN 1451) loaded as a web font on every device, Bahnschrift (Microsoft's DIN 1451, built into Windows) as the fallback. One family for all text, including tags, ids and numbers; D-DIN has regular and bold, so headings are bold and values regular. Self-host the D-DIN files before production rather than relying on a public font CDN.
- **Numbers**: tabular numerals everywhere (`font-feature-settings: "tnum"`). The value dominates; label above in `ink-muted`; target and source below in `caption`.
- **Density**: 13px body, 6px/12px cell padding, 12px gaps. Designed for a 13–16" laptop at 1366–1440 wide; collapses to two and then one column for tablets.

## Trend chart

Stacked lanes sharing one time axis (pressure, temperature, calculated, static, capacity), each with its own scale — the SCADA/oscilloscope pattern, not dual-axis overlays. A `Y` run bar on top shows compressor calls. Presets (Refrigerant, Air side, Pressure, Temperature, Performance, All) choose channels; the legend is also the live readout and switches to cursor values on hover. Saturation temperatures are dashed in their side's colour. Configured limits (max ESP) draw as dashed `caution` lines; targets as a faint band.

## Homeowner view

A second screen (group **Homeowner**) for the people who live with the equipment. Same tokens and state colours; different rules:

- **Plain language, no jargon.** "Air may be restricted", not "High TESP". Level words map to states: *Good* (`ok`), *Good to know* (`advisory`), *Check soon* (`caution`), *Service needed* (`fault`), *Offline* (`offline`). A sensor error is told to the homeowner as *Good to know* with "your contractor has been notified".
- **One headline, one next action.** The top card says what the system is doing and what to do about it, in a sentence.
- **Few numbers, all everyday ones**: inside and outside temperature, air from the vents, hours run. Pressures, superheat and static stay in the technician view; the one number a contractor needs appears once in a "For your contractor" line.
- **Still hedged.** "May be", "most often" — the same honesty as the technician diagnostics.
- **No health score.** Health is five named areas each with a word; the bar counts areas, it doesn't grade.
- Larger type (15px body, 28px headline) and more space than the technician view; one column below 860px, built for phones first.

## Iconography

None beyond state glyphs, a checkbox and a chevron. Text labels do the work.

## Extending

New measurements (compressor amps, voltage, filter ΔP, coil ΔP, extra probes) are new rows in an existing table and new channels in an existing lane; new systems or sites go in the status-bar system selector. Don't add cards.
