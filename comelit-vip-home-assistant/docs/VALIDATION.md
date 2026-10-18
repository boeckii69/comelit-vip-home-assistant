# Publication draft validation — 2026-10-18

Checked locally before packaging **0.2.0b1**:

- `python3 -m unittest discover -s tests -q`: 137 discovered, 134 passed, 3 Asterisk tests skipped because this command did not select a runtime.
- With `COMELIT_ASTERISK_RUNTIME` set, the 3 Asterisk runtime tests passed separately. Thus all 137 Python cases passed across these two runs.
- `node --test tests/test_card.cjs`: all 4 card lifecycle/audio tests passed.
- Fresh-setup form checked with HA API doubles: installation IP fields empty, forwarding/SIP/automatic mode off, recommended port 5062, gateway key masked, documentation placeholder supplied.
- Python parsing, JSON/YAML loading, relative Markdown links, image decoding/dimensions, translation-key coverage, shipped license notices, source-package structure and identifying-address/credential-pattern checks passed.
- Installer shell syntax passed.
- Byte comparison to the functional 0.1.12 source: CTP framing, native call control, command builders, audio codecs, native microphone handling, SIP wire/session/media and remote gate unchanged. The Asterisk gateway runtime is byte-identical to 0.1.1.

The project uses synthetic documentation-range addresses in fixtures. Native client changes concern configuration defaults and allowing an empty Loxone destination when forwarding is disabled. Setup text, translations, metadata, artwork and playback-card presentation are changed.

## Not claimed

This environment did not boot Home Assistant or build a Supervisor/Docker app image. The new community package was not installed on the reference hardware during preparation. No additional physical door action or live provider/mobile call was made. Existing field success concerns the underlying 0.1.12/0.1.1 versions as reported in [compatibility](COMPATIBILITY.md).

Repository identity was subsequently finalized for `boeckii69/comelit-vip-home-assistant` on 2026-10-18 and the strict project check passed. No remote repository or GitHub release was created by this metadata change. GitHub CI and a separate HA installation check are still required before marking the community package as a released beta.
