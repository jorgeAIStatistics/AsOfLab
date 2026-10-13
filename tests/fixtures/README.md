# Test fixtures

The handwritten fixture corpus lives inside the package, at
`src/asoflab/fixtures/cases/`, so that the command line and other projects can
run it (`asoflab fixtures list`, `asoflab fixtures check`). It is loaded with
`asoflab.fixtures.load_cases()`.

This directory is reserved for test-only data. Failing archives found by the
property tests are written to `tests/counterexamples/`, which is not committed;
turn one into a named fixture in the package if it captures a new failure mode.
