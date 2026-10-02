// tsconfig's `@/` for app-dir modules under node --test (LAB-13; the hook tests/b/actions.test.ts uses).
// Import it before dynamically importing a module that uses `@/`.
import * as nodeModule from "node:module";

type Resolved = { url: string; shortCircuit?: boolean };
type Resolve = (specifier: string, context: object) => Resolved;
const { registerHooks } = nodeModule as unknown as {
  registerHooks(hooks: { resolve(specifier: string, context: object, next: Resolve): Resolved }): void;
};
registerHooks({
  resolve: (specifier, context, next) =>
    next(specifier.startsWith("@/") ? new URL(`../../../${specifier.slice(2)}.ts`, import.meta.url).href : specifier, context),
});
