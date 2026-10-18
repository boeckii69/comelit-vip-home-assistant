# Attribution and dependencies

The project is MIT licensed, including the original door/audio project artwork. `custom_components/comelit_native/protocol/ctp.py` is derived from the MIT-licensed CTP implementation in [jfmlima/comelit-vip](https://github.com/jfmlima/comelit-vip), copyright 2026 Jorge Lima. The full upstream notice is in `THIRD_PARTY_LICENSE.txt` and is included inside the HACS-downloadable integration folder as well as at repository root.

The native integration uses Python's standard library and APIs supplied by Home Assistant; `requirements` is empty. Home Assistant's own license is not replaced by this project's license. Python `audioop` is used only as an independent test oracle on Python 3.12, not at runtime.

The optional audio app installs Asterisk, Python and CA certificates from Alpine Linux repositories during its local container build. Those packages retain their upstream/distribution licenses. In particular Asterisk is not relicensed under this repository's MIT license. No prebuilt container or manufacturer binary is shipped by this repository. Anyone distributing a built image must satisfy the licenses of all included packages, including corresponding-source obligations where applicable.

Documentation credits other community projects for research context; they are not imported dependencies. No Comelit PC program, proprietary DLL, firmware image, license key or crack is included. Comelit, ViP, Loxone and Home Assistant names identify compatible systems and do not imply endorsement.
