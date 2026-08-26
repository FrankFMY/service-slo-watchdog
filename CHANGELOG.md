# Changelog

## 0.1.0

- Initial public source release.
- Added configurable revision/readiness/liveness/dependency probes.
- Added bounded HTTP transport and private atomic alert state.
- Added report and quiet scheduler modes.
- Added fail-closed handling for deeply nested JSON, non-object/inconsistent state, unknown error values and duplicate metric series.
- Treat deeply nested state as corruption and preserve alert delivery when directory sync fails after atomic replacement.
