# Contributing

Start with a specific bug, device report or small change. Preserve the working native transport and keep unrelated cleanup separate from protocol changes. No packet capture is required for a normal support report.

## Development

The integration has no extra Python runtime packages. Use Python 3.12 for the independent `audioop` codec comparisons; the production codecs do not import it. Node runs the card lifecycle tests. Install validation helpers with:

```sh
python3 -m pip install -r requirements-dev.txt
python3 -m unittest discover -s tests -q
node --test tests/test_card.cjs
python3 scripts/check_project.py
```

Before the repository identity is finalized, use `python3 scripts/check_project.py --allow-unconfigured` for the project check. This exception is for the local publication draft only.

Tests use synthetic documentation-range addresses and loopback endpoints. No real Comelit command, microphone capture or internet SIP account is needed. Do not replace loopback destinations with a live door. The test's fake gateway credentials and generated TLS certificates are deliberately non-production fixtures.

The optional three Asterisk integration tests use `COMELIT_ASTERISK_RUNTIME` pointing to a compatible extracted Alpine filesystem with `lib/ld-musl-x86_64.so.1` and `usr/sbin/asterisk`. The reference runtime was Asterisk 20.11.1. Without it those tests are skipped, not counted as passing. HA-facing tests use API doubles; full Home Assistant/Supervisor installation remains a separate validation step.

`scripts/build_brand.py` regenerates the original PNG assets using Pillow and DejaVu Sans. These tools/fonts are development assets, not integration dependencies.

## Useful reports

Include device models, firmware, HA version, integration version, gateway version, phone OS/app version, whether the failing route is local or mobile, expected behavior and sanitized status attributes. For compatibility reports state separately: ringing, door action, local listening, local speech, idle-call start and external speech. Do not label an entire model family supported from one partial success.

Do not post HA tokens, gateway keys, SIP credentials, public/private installation addresses, MAC addresses, call recordings, full HA backups or private camera images. A text error/counter report is usually sufficient.

## Pull requests

Describe the problem, the resulting behavior and meaningful validation. Keep IDs compatible with existing config entries. A change must not open a door during setup, reconnect, ringing or an automatic retry. Media targets must come from the current validated session. Keep local allowlists and external access-mode disclosure intact.

Retain MIT notices and identify any third-party source. Do not include proprietary binaries or copy code from an incompatible license without resolving the licensing first. Contributions are accepted under this project's MIT license; upstream license notices remain intact.

## Release

See [Publishing](docs/PUBLISHING.md). CI runs tests and project validation. It does not publish automatically. A release is an explicit maintainer action, with results and remaining validation limits recorded in its notes.
