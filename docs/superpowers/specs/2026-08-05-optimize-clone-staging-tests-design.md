# Optimize Clone Staging Tests Design

- Date: 2026-08-05
- Status: Approved

## Goal

Reduce the staging-clone test cost in the default Python suite while preserving mandatory coverage
for the destructive local-database replacement contract.

## Design

Keep the critical-path clone tests in the default `make test` and `make check` runs. These tests must
cover explicit confirmation, local-only Docker targeting, validated dump publication, exact local
database replacement, safety-dump recovery, and mutation-free Django migration validation.

Mark the exhaustive failure, signal, atomic-publication, hostile-input, and concurrency variants with
a strict pytest marker. Default project test commands exclude that marker. A dedicated Make target
runs the complete clone-staging test file, including marked tests and the existing opt-in PostgreSQL
integrations when their environment flags are supplied.

The production script and its safety behavior do not change. The optimization changes only test
selection and developer commands.

## Success criteria

- The critical clone contract remains part of both default Python test commands.
- The complete clone test file remains runnable with one documented Make target.
- The isolated default-selected clone tests take no more than 15 seconds on the current workstation.
- The complete clone test file still passes.
- Repository contract tests protect the new marker and Make targets.
