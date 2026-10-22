# Bundled HAR schema

The 18 JSON files in this directory are unchanged copies of `lib/*.json` from
[Ahmad Nassri's har-schema repository](https://github.com/ahmadnassri/har-schema),
pinned to Git tree `c01b2e49edc33cd2cd22c980c57681be57bbb1f6` and retrieved on
2026-09-12. Source URL pattern:

`https://raw.githubusercontent.com/ahmadnassri/har-schema/c01b2e49edc33cd2cd22c980c57681be57bbb1f6/lib/<name>.json`

The accompanying `LICENSE` is the upstream ISC license, copyright Ahmad Nassri,
2015. These are community-maintained JSON schemas, not a W3C recommendation or
an official certification of HAR conformance. The W3C HAR historical draft was
abandoned. The HAR 1.2 prose reference is available in the maintainer's
[har-spec repository](https://github.com/ahmadnassri/har-spec/blob/master/versions/1.2.md).

## Runtime corrections and additional checks

The validator uses JSON Schema Draft 6. All schema references resolve from the
bundled files; neither a HAR's `$schema` property nor a network URL is fetched.
The upstream files remain unchanged. `validation.py` applies these corrections
in memory:

- Add object types missing from the cache and timings schemas.
- Replace the unrecognized `min` keyword with `minimum` in property schemas.
- Move the postData parameter object rules into an array `items` schema.

Additional checks reject invalid calendar dates and missing timezone offsets,
negative request durations, invalid timing sentinel values, negative sizes other
than unknown `-1`, duplicate page IDs, invalid page references, invalid embedded
base64, duplicate JSON keys, nonfinite numbers, and integers beyond finite
floating-point range. Timestamp/URI/IP format checkers use the Python standard
library. URI checking is a practical absolute-URI check rather than a complete
RFC grammar implementation. Unknown HAR extension fields are permitted.

Timing sums exclude SSL because SSL is already part of connection time. Sum
discrepancies exceeding 1 ms or 1% produce warnings, accommodating exporter
rounding and incomplete timings. Exporters that produce both postData text and
params also produce a warning rather than being rejected.

An empty entries array is structurally valid but has a capture-quality warning.
Missing response bodies and status zero are warnings. Body counts measure the
presence of string content, including empty strings, rather than completeness.
Original body sizes are never compared with embedded string byte lengths:
compression, transcoding, omitted data, and redaction can change those lengths.

Validation does not prove browser-import compatibility, workflow completion,
complete traffic coverage, privacy, or sanitizer effectiveness. It loads the HAR
in memory and is intended for locally captured experiment files, not a public
service accepting arbitrarily large untrusted uploads. Diagnostics are limited
to fixed schema paths and rules; they exclude captured values and input paths.
