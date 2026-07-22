# NovelForge Windows Release

NovelForge uses semantic versions. The first public version is `0.1.0`, published with the Git tag `v0.1.0`.

Pushing a matching `v*.*.*` tag starts the Windows Release workflow. The workflow verifies version consistency, builds MSI and NSIS installers, tests installation and startup on a clean Windows runner, tests an upgrade from the previous release (or a synthetic `0.0.9` baseline for the first release), verifies uninstall and user-data preservation, creates SHA-256 checksums, and publishes a GitHub Release.

## Code signing

Unsigned packages can be produced while the project is in early testing, but Windows SmartScreen will show stronger warnings. A public release should use an Authenticode code-signing certificate from a trusted certificate authority.

Configure these GitHub Actions secrets:

- `WINDOWS_CERTIFICATE`: Base64-encoded contents of the `.pfx` certificate file.
- `WINDOWS_CERTIFICATE_PASSWORD`: Password used to protect the `.pfx` file.

The certificate must permit code signing and should identify the legal publisher name users will recognize. An EV code-signing certificate usually develops SmartScreen reputation faster, while a standard organization-validated certificate is less expensive. The workflow imports the certificate only into the temporary Windows runner, signs with SHA-256, uses DigiCert's timestamp service, verifies the resulting signatures, and then discards the runner.
