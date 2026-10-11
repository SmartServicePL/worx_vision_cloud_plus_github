# Changelog

## Unreleased

## 1.5.1 - 2026-10-11

- The default blade service interval is now 100 hours of blade runtime instead
  of 12 hours, following the Worx Wiki for Vision mowers (replace the blades
  after about 100-200 operating hours). With 12 hours the "Blade replacement
  due" alert came back almost every day. A value you set on the mower's
  device page is kept.

## 1.5.0 - 2026-10-11

- Mower commands (start, pause, dock), the one-time mowing action, edge
  cutting, buttons, switches and numbers now report every Worx cloud failure
  as a readable, translated Home Assistant error. Scripts and automations can
  handle it with `continue_on_error`; before, cloud timeouts and rate limits
  stopped the automation with an unexpected error.
- A command that reached the Worx cloud is no longer reported as failed only
  because the state refresh after it timed out.
- Start, pause and dock reconnect a dropped Worx MQTT connection before
  sending, like one-time mowing already did. A command that failed because the
  connection was down is retried once after reconnecting. Reconnects are
  serialized, so two simultaneous commands no longer open two connections.
- A rejected Worx login during a command or a periodic refresh now starts
  re-authentication.
- When the Worx cloud connection is lost and cannot be re-established, the
  entities become unavailable instead of showing the last state indefinitely.
  They recover with the next pushed update or periodic refresh.
- The `start_one_time_mowing` action is now registered when Home Assistant
  starts, also while the Worx cloud is unreachable; it then fails with a clear
  "not loaded" error instead of being unknown to automations.
- A Worx cloud rate limit pauses the statistics, map and firmware requests for
  one hour and is logged as a warning.
- Added per-mower maintenance interval numbers (blade hours, optional blade
  days, battery cycles) and separate "Blade replacement due" and "Battery
  service due" binary sensors, so a due blade service no longer hides a due
  battery service. The defaults keep the previous thresholds (12 h of blade
  runtime, 500 battery cycles); the existing maintenance status sensor stays
  and gains the due flags and thresholds as attributes.
- Added integration options: raw payload entities can now be turned on or off
  without removing the integration.
- One-time mowing with zones on Vision/RTK mowers writes a warning to the log,
  because some firmware ignores the zone selection (issue #5).
- The `rtk_at_station` mower attribute is always present (empty when unknown).
- RTK map placeholder texts follow the Home Assistant language, and the map
  camera no longer writes its state on every image request.
- A warning is logged at setup when the installed pyworxcloud lacks the private
  API used for statistics, maps and firmware details.
- Raised the minimum Home Assistant version in `hacs.json` to 2025.2.0, the
  first release with the entity platform API used by this integration.
- Corrected the lawn mower entity ID in the documentation and documented the
  mower states and attributes used by automations.

## 1.4.1 - 2026-09-26

- Fixed the "Expose all raw payload fields as entities" option: it was reset to
  off on every start, so raw debug entities could never be enabled.
- Fixed cloud statistics (covered area, maintenance counters) that could stop
  refreshing while the mower was working: frequent MQTT pushes no longer
  postpone the periodic Worx REST refresh.
- Fixed the RTK address sensor sending practically exact coordinates to
  OpenStreetMap Nominatim and repeating the lookup on almost every move; lookups
  now use coordinates rounded to about 11 m and are cached accordingly.
- Added re-authentication: when Worx rejects the saved login, Home Assistant
  asks for the new password instead of requiring the integration to be removed.
- Fixed a Worx cloud connection left open when the first data refresh failed
  during setup.
- Fixed unloading so runtime data is kept when Home Assistant cannot unload the
  platforms.
- One-time mowing runtime, edge cutting and zone selection are now kept after a
  Home Assistant restart.
- The one-time mowing service now rejects a runtime of 0 minutes unless an
  edge-only pass is requested.
- Corrected the integration address in the reverse-geocoding User-Agent.

## 1.4.0 - 2026-08-31

- Fixed the current-zone sensor on RTK mowers whose protocol reports the
  placeholder value `0`; the sensor now prefers the live RTK map polygon while
  preserving zone `0` for older boundary-wire mowers.
- Replaced ambiguous RTK map `zone_*` attributes with explicit
  `current_zone_*`, `first_zone_*` and `zone_count` metadata.
- Updated Home Assistant compatibility by using the modern percentage unit and
  device-registry lookup APIs while retaining compatibility with older
  supported Home Assistant versions.
- Added translated service names, descriptions and fields for all ten supported
  languages, and completed localized dynamic zone labels.
- Marked Vision/RTK one-time zone selection as experimental because some current
  firmware ignores zone IDs sent through the available MQTT command.
- Added regression tests for RTK placeholder-zone handling and legacy zone-zero
  compatibility.

## 1.3.1 - 2026-07-19

- Reconnected the Worx MQTT session before mower commands when the cloud connection is stale.
- Made the post-command device refresh best effort, so an accepted edge-cut or one-time mowing command is not reported as failed only because the immediate refresh timed out.
- Improved reliability of the smart mowing edge-first automation flow after recent Worx Cloud / mower firmware changes.

## 1.3.0 - 2026-07-13

- Updated `pyworxcloud` from `6.4.1` to `6.4.2` for the latest RTK position
  fallback and time-extension handling.
- Added a Vision edge-distance number entity and improved Smart edge cutting
  state detection from protocol 1 RTK zone cut settings.
- Added diagnostic binary sensors for PIN setting support, Vision disable
  support, random map pattern support, map training support and diagnostic
  upload support.
- Fixed the current-zone sensor for RTK/Vision mowers by resolving the live RTK
  position against the mower map zones when the legacy Worx zone field is empty.

## 1.2.0 - 2026-07-06

- Added a next-schedule timestamp sensor with timezone-aware fallback
  calculation for enabled native weekly schedules.
- Corrected `area_mowed` handling: the Worx value is now exposed as cumulative
  covered area instead of being mislabeled as today's area.
- Added persisted daily cloud area/progress based on a local-midnight baseline.
- Added clearly labeled estimated daily area and progress based on locally
  observed blade-active time and long-term mowing efficiency.
- Changed mowing efficiency to prefer cumulative blade-active time over total
  mower runtime.
- Added a cloud-statistics update timestamp and refreshed private REST data
  every five minutes.
- Fixed coordinator notifications for REST-enriched data by enabling
  `always_update`.
- Passed the config entry explicitly to the coordinator for Home Assistant
  2026.8 compatibility.
- Stopped treating the Worx account e-mail as a Home Assistant area, removed
  account prefixes from suggested device names and migrated the newly added
  statistic entity IDs away from that private prefix.
- Removed the deprecated device-tracker `battery_level` property while keeping
  the compatibility attribute, preventing a Home Assistant 2027.7 break.
- Added unit tests for schedule fallback, counter reset, day rollover and
  mowing across midnight.

## 1.1.0 - 2026-07-06

- Updated `pyworxcloud` from `6.3.6` to `6.4.1`.
- Added German, French, Dutch, Spanish, Italian, Swedish, Norwegian and Danish
  translations while keeping Polish and English.
- Made entity names, enum sensor states and one-time mowing zone options
  translatable.
- Localized schedule summaries, weekday labels and calendar events in all
  supported languages.
- Updated the device tracker to use the current Home Assistant imports.
- Simplified the primary mower entity name according to Home Assistant naming
  conventions without changing its entity ID.

## 1.0.11 - 2026-06-18

- Allowed the one-time mowing service to accept `runtime: 0`, so automations can explicitly start an edge-only pass after normal mowing.
- Updated the Home Assistant service description to show `runtime: 0` as the supported edge-only mode.
- Mapped Vision Cloud `runtime: 0` with `edge_cut: true` to the dedicated edge-cut command (`cmd: 101`), while keeping normal one-time mowing on the app-like `cmd: 10` payload.

## 1.0.10 - 2026-06-17

- Changed Vision one-time mowing with edge cutting back to the app-like one-time mowing payload (`cmd: 10` with `cfg.cut.b: 1`) so the mower performs normal mowing first and edge cutting at the end instead of doing an edge-only run.

## 1.0.9 - 2026-06-13

- Changed Vision one-time mowing with edge cutting and no selected zones to use the firmware command that starts edge cutting followed by the normal mowing cycle.
- Kept the standalone edge-cut button edge-only by continuing to use the zero-minute one-time mowing command for that button.

## 1.0.8 - 2026-06-12

- Added the official HACS validation workflow required for default HACS repository submissions.
- Updated HACS repository metadata so the integration passes the current HACS Action checks.

## 1.0.7 - 2026-06-12

- Removed RTK-based status overriding so mower state always follows the raw Worx Cloud status.
- Kept RTK station proximity as diagnostic attributes for automations without changing the displayed mower status.
- Allowed the one-time mowing service to run for 1 minute so automations can send a short status-refresh command when Worx Cloud gets stuck.
- Increased RTK address reverse-geocoding precision to 7 decimal places and kept the address based only on RTK coordinates.

## 1.0.6 - 2026-06-11

- Improved the RTK map trail so recent mower movement is rendered as a darker mowed grass swath instead of a thin GPS line.
- Calculated the mowed swath width from the mower model cutting width and the current map scale; WR308E/WR303E-class mowers use 18 cm.
- Clipped the mowed swath to the lawn contour so it stays inside the mapped grass area.

## 1.0.5 - 2026-06-11

- Added RTK station-based status correction so Home Assistant can show the mower as docked when Worx Cloud is stuck on stale mowing/returning/searching-home states.
- Preserved cached RTK map and product details across MQTT-only push updates so status correction keeps access to the base station marker.
- Changed the Vision edge-cut button to send a zero-minute one-time schedule with edge cutting enabled instead of `cmd:101`, because firmware 3.46.x can continue into full mowing after `cmd:101`.
- Added one-time mowing controls and service with runtime, edge-cut and optional RTK zone selection.
- Added a robot-lifted binary sensor based on Worx Cloud `lifted` and `upside down` error states.
- Removed the unavailable schedule edge procedure entities.
- Removed the radio link validation pending binary sensor.
- Removed the duplicate read-only lawn perimeter sensor.
- Added Polish state labels for the status and mowing-readiness sensors.

## 1.0.4

- Fixed the edge cutting button for Vision mowers whose Worx Cloud schedule payload does not expose the derived edge-cut capability.
- The integration now sends the border-cut MQTT command directly instead of relying on `pyworxcloud.edgecut()`, which could silently do nothing.

## 1.0.3

- Removed the `auto_schedule` switch completely.
- Added entity-registry cleanup for the removed automatic schedule switch.

## 1.0.2

- Removed the unreliable battery charging binary sensor.
- Removed the unreliable distance covered sensor.
- Added entity-registry cleanup for both removed entities.

## 1.0.1

- Fixed mower command refresh for `pyworxcloud==6.3.6` by removing an unsupported `timeout` argument from device update requests.
- Restored button and mower commands that previously failed with `WorxCloud.update() got an unexpected keyword argument 'timeout'`.

## 1.0.0

- Promoted the integration to the first stable `1.0.0` release.
- Added a native Home Assistant firmware update entity with release notes and OTA install support when exposed by Worx Cloud.
- Added configurable rain delay, schedule time-extension, lawn area and lawn perimeter number entities.
- Added switches for firmware auto update, mower lock, native schedule and Worx auto schedule.
- Added cloud/MQTT diagnostics, mowing-readiness status, API capabilities and push notification state sensors.
- Added extended mowing statistics: lawn area/perimeter, distance covered, efficiency and mower time at home, charging and in error.
- Added maintenance tracking for blade runtime and battery cycles, including reset timestamps and a battery cycle reset button.
- Added recent RTK trail storage, a diagnostic trail sensor and a trail overlay on the RTK map camera.

## 0.3.5

- Added an on-demand edge cutting button that starts the mower in border-only cutting mode.

## 0.3.4

- Added root-level `icon.png` and `logo.png` compatibility files so HACS can resolve the repository image in places that do not read `brand/icon.png`.
- Updated the release workflow to publish icon-only fixes.

## 0.3.3

- Added Home Assistant switches for Smart edge cutting, Save the hedgehogs and schedule edge procedure.
- Renamed the Polish rain binary sensor label to `Czujnik opadów deszczu`.
- Removed the standard total driven distance sensor because the Worx payload does not update it reliably.
- Added entity-registry cleanup for the removed total driven distance sensor.
- Added integration-root icon and logo files so Home Assistant and HACS update cards can resolve the brand image more reliably.

## 0.3.2

- Moved Smart mowing schedule blueprint to a separate automation repository.
- Updated documentation to link to the separated automation repository.

## 0.3.1

- Added Smart mowing schedule Home Assistant blueprint.
- Added My Home Assistant import button for the blueprint.
- Added blueprint setup documentation and optional helper package example.

## 0.3.0

- Added diagnostic entities for Smart edge cutting, Save the hedgehogs and schedule edge procedure API fields.
- Added button to reset blade runtime after blade replacement.

## 0.2.2

- Added root-level HACS brand assets so the repository icon appears in HACS.

## 0.2.1

- Updated integration brand icon and logo assets.

## 0.2.0

- Added disabled-by-default RTK address sensor using OpenStreetMap Nominatim reverse geocoding.
- Added 24-hour address lookup cache, rounded-coordinate lookups and a one-request-per-second geocoding throttle.

## 0.1.0

- Initial public release.
- Added Home Assistant `lawn_mower` support.
- Added useful sensors and binary sensors.
- Added mowing schedule sensor and calendar entity.
- Added RTK map camera rendered from Worx map API data.
- Added RTK position `device_tracker`.
- Added daily progress, remaining progress and mowed area sensors when available.
- Added Polish and English translations.
- Added integration icon and Smart Service attribution.
