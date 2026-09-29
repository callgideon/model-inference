// LAB-E2E's harness, run without a stack (in lab-test and its mutant list): what the no-JS browser reads
// from served HTML and what it sends back. A harness that reads text a user cannot see, drops a hidden
// action field or keeps a cleared session would let the stack suites pass against a broken page.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import test from "node:test";
import { Browser, decode, form, forms, section, text } from "./harness.ts";

const PAGE = `<html><head><style>.x{}</style><script>var hidden = "script text";</script></head><body>
<h1>Releases</h1><p>1<!-- -->0 items</p><p>It&#x27;s <!-- -->A &amp; B&nbsp;&lt;ok&gt; &quot;q&quot; &#39;s&#39;</p>
<section aria-label="lab:policy:1"><dl><dt>Status</dt><dd>running</dd></dl>
<form action="" encType="multipart/form-data" method="POST"><input type="hidden" name="$ACTION_ID_abc"/>
<input type="hidden" name="policyRef" value="lab:policy:1"/><input type="hidden" name="fence" value="3"/>
<input type="checkbox" name="agree" value="yes"/><select name="kind"><option value="expand">E</option><option value="rollback" selected="">R</option></select>
<textarea name="rows">a &amp; b</textarea><input name="note"/><button type="submit">Propose rollback</button></form></section>
<section aria-label="lab:policy:2"><form><input type="hidden" name="$ACTION_ID_abc"/><select name="kind"><option value="expand">E</option></select><button>Propose expansion</button></form></section>
</body></html>`;

test("E2E-H01 the page's text is what a reader sees: no script or style, React's separators dropped, entities decoded", () => {
  const seen = text(PAGE);
  assert.ok(seen.startsWith("Releases 10 items It's A & B <ok> \"q\" 's' Status running"), seen);
  assert.ok(!seen.includes("script text") && !seen.includes(".x{}") && !seen.includes("<!--"));
  assert.equal(decode("&#x41;&#66;&amp;&unknown;"), "AB&&unknown;");
});

test("E2E-H02 a form submits what a browser would: hidden action fields, the selected option, the textarea, never an unticked box", () => {
  const [first, second] = forms(PAGE);
  assert.deepEqual(first.fields, [["$ACTION_ID_abc", ""], ["policyRef", "lab:policy:1"], ["fence", "3"], ["note", ""], ["kind", "rollback"], ["rows", "a & b"]]);
  assert.deepEqual(first.names, ["$ACTION_ID_abc", "policyRef", "fence", "agree", "note", "kind", "rows"]);
  assert.deepEqual(second.fields, [["$ACTION_ID_abc", ""], ["kind", "expand"]], "no selected option: the first");
  assert.deepEqual(form(PAGE, /Propose expansion/), second);
  assert.throws(() => form(PAGE, /Propose/), /one form matching/, "two matches are an error, never the first");
  assert.throws(() => form(PAGE, /Launch/), /one form matching/);
});

test("E2E-H03 a record's section is found by its own label only", () => {
  assert.equal(text(section(PAGE, "lab:policy:2")!), "E Propose expansion");
  const one = text(section(PAGE, "lab:policy:1")!);
  assert.ok(one.startsWith("Status running") && one.endsWith("Propose rollback"), one);
  assert.equal(section(PAGE, "lab:policy:9"), null);
});

test("E2E-H04 the browser keeps the cookies it is given, drops a cleared one, and posts a form as multipart from its own origin, unfollowed", async (t) => {
  const seen: { method?: string; cookie?: string; origin?: string; body: string }[] = [];
  const server = createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      seen.push({ method: req.method, cookie: req.headers.cookie, origin: req.headers.origin, body });
      const cookies = seen.length === 1 ? ["sb-lab=token; Path=/", "ws=1; Path=/"] : req.method === "POST" ? ["ws=; Path=/; Max-Age=0"] : [];
      res.writeHead(req.method === "POST" ? 303 : 200, { "set-cookie": cookies, location: "/next", "content-type": "text/html" });
      res.end("<p>ok</p>");
    });
  }).listen(0, "127.0.0.1");
  t.after(() => server.close());
  await new Promise((done) => server.once("listening", done));
  const b = new Browser(`http://127.0.0.1:${(server.address() as { port: number }).port}`);
  const got = await b.get("/");
  assert.deepEqual([got.status, got.text, [...b.cookies]], [200, "ok", [["sb-lab", "token"], ["ws", "1"]]]);
  await assert.rejects(b.submit("/", forms(PAGE)[0], { missing: "x" }), /no field missing/);
  const sent = await b.submit("/", forms(PAGE)[0], { note: "hi", kind: "expand" });
  assert.deepEqual([sent.status, sent.location, [...b.cookies]], [303, "/next", [["sb-lab", "token"]]], "a redirect is not followed; a cleared cookie is dropped");
  const post = seen[1];
  assert.deepEqual([post.method, post.cookie, post.origin], ["POST", "sb-lab=token; ws=1", b.base]);
  for (const [name, value] of [["$ACTION_ID_abc", ""], ["policyRef", "lab:policy:1"], ["note", "hi"], ["kind", "expand"]])
    assert.match(post.body, new RegExp(`name="${name.replace("$", "\\$")}"\\r\\n\\r\\n${value}\\r\\n`), name);
  assert.doesNotMatch(post.body, /name="kind"\r\n\r\nrollback/, "an override replaces the form's own value");
});
