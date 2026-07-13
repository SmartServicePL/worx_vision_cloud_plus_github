# Worx Vision Cloud PLUS

Custom Home Assistant integration for Worx Landroid Vision / Vision Cloud / RTK mowers.

Install the full repository with HACS or copy `custom_components/worx_vision_cloud` into your Home Assistant `custom_components` directory.

Main features:

- native `lawn_mower` entity
- native firmware update entity
- useful mower sensors, binary sensors and firmware-capability diagnostics
- rain delay, time-extension, lawn area, lawn perimeter and Vision edge-distance number entities
- mower lock, firmware auto-update and schedule switches
- mowing schedule summary, next start sensor and calendar
- RTK map camera
- recent RTK trail overlay
- RTK robot position tracker
- optional RTK address sensor, disabled by default
- one-time mowing controls with runtime, edge cutting and optional RTK zone selection
- edge cutting and hedgehog protection switches
- on-demand edge cutting button
- blade runtime reset button
- battery cycle reset button
- separate cumulative, daily cloud and locally estimated mowing statistics
- Polish, English, German, French, Dutch, Spanish, Italian, Swedish, Norwegian
  Bokmål and Danish translations

Integration prepared by **Smart Service**.

Worx's `area_mowed` value is cumulative covered area and can update late.
Daily cloud values use a persisted midnight baseline. Estimated values use
locally observed blade-active time and are clearly labeled as estimates.
