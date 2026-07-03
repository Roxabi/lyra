// Permanent tripwire — do not delete (PR #2184).
//
// The browser app program (tsconfig.app.json) must never see Node ambient
// globals. The test/node programs allow them; the split is enforced only by
// filename globs, so a stray vitest import from non-test src (or a widened
// types array) would silently re-open the leak. This file makes that loud:
// while the app program is clean, `process` is an unresolved name and the
// directive below suppresses exactly that error. If Node ambients ever leak
// back in, `process` resolves, the directive becomes unused, and tsc fails
// with "Unused '@ts-expect-error' directive".
//
// This file is only a member of the app program: the test program includes
// nothing but *.test/*.spec/test-setup, and vitest never executes it.

// @ts-expect-error node ambients must not leak into the browser program (tripwire — see PR #2184)
export type NodeAmbientTripwire = typeof process;
