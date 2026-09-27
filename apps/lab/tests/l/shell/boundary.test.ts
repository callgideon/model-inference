// L1 LAB-ACCESS + SPLIT-CONTRACT, read as source: a layout does not stop its pages or actions from
// running (Next's Partial Rendering), so every entry point calls the guard itself.
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import test from "node:test";
import ts from "typescript";

const lab = resolve(import.meta.dirname, "../../..");
const GUARD = /\b(requireProviderWorkspace|requireProviderSession|providerAccessForRequest)\(/;

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (/^(node_modules|\.next)$/.test(name)) return [];
    return statSync(path).isDirectory() ? walk(path) : [path];
  });
}
const sources = () =>
  ["app", "lib"].flatMap((d) => walk(join(lab, d))).filter((p) => /\.(ts|tsx)$/.test(p) && !/\.test\.ts$/.test(p))
    .map((path) => ({ path: relative(lab, path), source: readFileSync(path, "utf8") }));

/** A directive prologue (leading string statements) that says "use server". */
function serverPrologue(statements: readonly ts.Statement[]): boolean {
  for (const s of statements) {
    if (!ts.isExpressionStatement(s) || !ts.isStringLiteral(s.expression)) return false;
    if (s.expression.text === "use server") return true;
  }
  return false;
}

/** Anything a "use server" file exports at runtime is a server action (types are erased). */
function exported(s: ts.Statement): boolean {
  if (ts.isExportAssignment(s)) return true;
  if (ts.isExportDeclaration(s)) return !s.isTypeOnly;
  if (ts.isTypeAliasDeclaration(s) || ts.isInterfaceDeclaration(s)) return false;
  return ts.canHaveModifiers(s) && (ts.getModifiers(s) ?? []).some((m) => m.kind === ts.SyntaxKind.ExportKeyword);
}

/**
 * The entry points that run before any provider session exists (WR-L1-6), named exactly: signing in
 * and out, and the email-link callback. Anything else these files export still needs the guard.
 */
export const PUBLIC: Readonly<Record<string, readonly string[]>> = {
  "lib/auth/sign-in.ts": ["signIn", "signOut"],
  "app/auth/callback/route.ts": ["GET"],
};

function names(s: ts.Statement): string[] {
  const modifiers = ts.canHaveModifiers(s) ? (ts.getModifiers(s) ?? []) : [];
  if (modifiers.some((m) => m.kind === ts.SyntaxKind.DefaultKeyword)) return ["default"];
  if (ts.isFunctionDeclaration(s) && s.name) return [s.name.text];
  if (ts.isVariableStatement(s)) return s.declarationList.declarations.map((d) => d.name.getText());
  if (ts.isExportDeclaration(s) && s.exportClause && ts.isNamedExports(s.exportClause)) return s.exportClause.elements.map((e) => e.name.text);
  return ["default"];
}
const isPublic = (path: string, s: ts.Statement) => path in PUBLIC && names(s).every((n) => PUBLIC[path].includes(n));

/**
 * Entry points with no guard: pages, route handlers and provider layouts (`path`), and every server
 * action (`path:line`): each runtime export of a "use server" file (async function, arrow const,
 * default, export list) and each function whose body opens with "use server" (an inline action).
 * An export list or `export default name` is flagged even when its target is guarded: write the
 * guard in the exported function itself.
 */
export function unguarded(files: { path: string; source: string }[]): string[] {
  const out: string[] = [];
  for (const { path, source } of files) {
    const file = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true);
    const flag = (node: ts.Node) => {
      if (!GUARD.test(node.getText(file))) out.push(`${path}:${file.getLineAndCharacterOfPosition(node.getStart(file)).line + 1}`);
    };
    const serverFile = serverPrologue(file.statements);
    if (!serverFile && (/(^|\/)(page\.tsx|route\.ts)$/.test(path) || /^app\/\(provider\)\/.*layout\.tsx$/.test(path))) {
      const exports = file.statements.filter(exported);
      if (!GUARD.test(source) && !(path in PUBLIC && exports.every((s) => isPublic(path, s)))) out.push(path);
    }
    if (serverFile) file.statements.filter((s) => exported(s) && !isPublic(path, s)).forEach(flag);
    const inline = (node: ts.Node): void => {
      const fn = ts.isFunctionDeclaration(node) || ts.isFunctionExpression(node) || ts.isArrowFunction(node) || ts.isMethodDeclaration(node);
      if (fn && node.body && ts.isBlock(node.body) && serverPrologue(node.body.statements)) flag(node.body);
      ts.forEachChild(node, inline);
    };
    inline(file);
  }
  return out;
}

test("L1-B01 every page, route, provider layout and server action calls the provider guard", () => {
  const files = sources();
  assert.ok(files.some((f) => f.path === "lib/auth/actions.ts"), "the selection action exists");
  assert.deepEqual(unguarded(files), []);
  // The check itself: an unguarded page, and every server-action form Next accepts, are caught.
  const server = (body: string) => `"use server";\n${body}`;
  assert.deepEqual(
    unguarded([
      { path: "app/(provider)/x/page.tsx", source: "export default function P() {}" },
      { path: "lib/ok.ts", source: server("export async function ok() { await requireProviderSession(); }\nexport type T = string;") },
      { path: "lib/fn.ts", source: server("export async function bad() {}\nasync function helper() { await requireProviderSession(); }") },
      { path: "lib/arrow.ts", source: server("export const bad = async (d: FormData) => String(d.get(\"x\"));") },
      { path: "lib/default.ts", source: server("export default async function bad() {}") },
      { path: "lib/list.ts", source: server("async function bad() {}\nexport { bad };") },
      { path: "lib/comment.ts", source: `// a comment first\n${server("export async function bad() {}")}` },
      {
        path: "app/(provider)/y/page.tsx",
        source: 'export default async function P() { await requireProviderWorkspace(); return <form action={async () => { "use server"; }} />; }',
      },
      // WR-L1-6: only the named public entry points are exempt, never a neighbour in the same file.
      { path: "lib/auth/sign-in.ts", source: server("export async function signIn() {}\nexport async function peek() {}") },
      { path: "app/auth/callback/route.ts", source: 'export { authCallback as GET, peek as POST } from "x";' },
      { path: "app/auth/other/route.ts", source: 'export { authCallback as GET } from "x";' },
    ]),
    [
      "app/(provider)/x/page.tsx", "lib/fn.ts:2", "lib/arrow.ts:2", "lib/default.ts:2", "lib/list.ts:3", "lib/comment.ts:3",
      "app/(provider)/y/page.tsx:1", "lib/auth/sign-in.ts:3", "app/auth/callback/route.ts", "app/auth/other/route.ts",
    ],
  );
});

test("L1-B02 the provider layout renders its children only for a ready workspace", () => {
  const layout = readFileSync(join(lab, "app/(provider)/layout.tsx"), "utf8");
  assert.equal(layout.split("{children}").length - 1, 1);
  const ready = layout.indexOf('access.kind === "ready"');
  assert.ok(ready !== -1 && ready < layout.indexOf("{children}"));
  assert.match(layout, /export const dynamic = "force-dynamic";/);
});

test("L1-B05 a signed-out visitor gets the Lab sign-in form, and every signed-in state can sign out", () => {
  const layout = readFileSync(join(lab, "app/(provider)/layout.tsx"), "utf8");
  const signedOut = layout.indexOf('access.kind === "signed-out"');
  assert.ok(signedOut !== -1 && signedOut < layout.indexOf("<SignInForm />"));
  assert.equal(layout.split("<SignOut />").length - 1, 3, "ready, select and denied each offer sign-out");
  const form = readFileSync(join(lab, "lib/auth/sign-in-form.tsx"), "utf8");
  assert.match(form, /useActionState\(signIn, null\)/);
  assert.match(form, /SIGN_IN_COPY\[state\.error\]/);
});

test("L1-B03 the selection action stores the membership it validated, never the submitted value", () => {
  const action = readFileSync(join(lab, "lib/auth/actions.ts"), "utf8");
  assert.match(action, /chooseWorkspace\(access, formData\.get\("providerId"\)\)/);
  assert.match(action, /\.set\(WORKSPACE_COOKIE, chosen\.providerId, workspaceCookieOptions\(config\)\)/);
  assert.equal(action.split(".set(").length - 1, 1);
});

test("L1-B04 the Lab never imports the App: shared code comes only from packages/shared", () => {
  const offenders = sources().filter(({ source }) => /from\s+["'][^"']*apps\/app|from\s+["'](\.\.\/)+app\//.test(source));
  assert.deepEqual(offenders.map((f) => f.path), []);
});
