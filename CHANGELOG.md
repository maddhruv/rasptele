# Changelog

All notable user-visible changes to Rasptele are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and releases use [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-07-19

### Changed

- Consolidated Docker Compose, Portainer, and Coolify deployment into one pinned release manifest.
- Replaced the required YAML configuration file with environment variables and safe defaults.
- Stable container releases now also publish the optional `latest` tag.

### Removed

- Removed platform-specific Compose files and the YAML configuration interface.

## [0.2.0] - 2026-07-19

### Added

- Pi-hole v6 status, temporary blocking disablement, immediate re-enablement, and availability alerts.
- Deployment regression checks that verify secret boundaries across all Compose files.
- Changelog-first release automation with release-it, synchronized versions, and gated GitHub Releases.

### Changed

- Updated the container vulnerability scanner action.

## [0.1.1] - 2026-07-18

### Added

- Portainer deployment through released GitHub Container Registry images.
- Deployment documentation for Docker Compose, Coolify, and Portainer.

### Fixed

- Loaded the image before scanning it in continuous integration.

## [0.1.0] - 2026-07-18

### Added

- Single-user Telegram commands for Raspberry Pi host and Docker container status.
- Allowlisted container restarts with expiring, single-use confirmation.
- Stateful host and container alerts with durable delivery and local auditing.
- Docker guard sidecar and independent watchdog services.
- Docker Compose and Coolify deployment definitions.

[Unreleased]: https://github.com/maddhruv/rasptele/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/maddhruv/rasptele/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/maddhruv/rasptele/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/maddhruv/rasptele/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/maddhruv/rasptele/releases/tag/v0.1.0
