// UX-11 accessibility probe: one browser-side expression (page.evaluate) both apps' a11y suites run on
// their synthetic harness pages. It reads the rendered page only; it changes nothing. Returned:
//   overflow  - the page scrolls sideways (WCAG 1.4.10 reflow; 200% zoom = half the CSS width);
//   unnamed   - visible controls with no accessible name (WCAG 4.1.2);
//   contrast  - visible text below 4.5:1, or 3:1 when large (WCAG 1.4.3), against its first opaque
//               ancestor background (a background image or gradient is not judged: reported as such);
//   motion    - movement longer than 10 ms (a running animation/transition of transform, offsets or
//               size, or a declared animation whose keyframes move the element) - read with the page
//               opened under prefers-reduced-motion: reduce, right after the interaction; colour and
//               opacity changes are not motion; a pending indicator (role=status, aria-busy or an
//               aria-hidden spinner) is exempt (02-foundations: "no continuous animation beyond real
//               pending indicators").
// Not a substitute for a screen reader or a person: the report says what it does not check.
(() => {
  const canvas = document.createElement("canvas").getContext("2d", { willReadFrequently: true });
  const rgba = (css) => {
    canvas.clearRect(0, 0, 1, 1);
    canvas.fillStyle = "#000";
    canvas.fillStyle = css;
    canvas.fillRect(0, 0, 1, 1);
    const [r, g, b, a] = canvas.getImageData(0, 0, 1, 1).data;
    return [r, g, b, a / 255];
  };
  const lum = ([r, g, b]) => {
    const c = (v) => ((v /= 255) <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
    return 0.2126 * c(r) + 0.7152 * c(g) + 0.0722 * c(b);
  };
  const over = (top, under) => top.slice(0, 3).map((v, i) => v * top[3] + under[i] * (1 - top[3]));
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && el.checkVisibility({ opacityProperty: true, visibilityProperty: true });
  };
  /** Laid out and not hidden: an element fading in from opacity 0 still moves, so motion ignores opacity. */
  const boxed = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && el.checkVisibility({ visibilityProperty: true });
  };
  const label = (el) => `${el.tagName.toLowerCase()}${el.id ? `#${el.id}` : ""} "${(el.textContent ?? "").trim().slice(0, 40)}"`;
  /** The opaque colour behind `el`: its ancestors' backgrounds composited bottom-up; null over an image. */
  function backdrop(el) {
    const layers = [];
    for (let n = el; n; n = n.parentElement) {
      const s = getComputedStyle(n);
      if (s.backgroundImage !== "none") return null;
      const c = rgba(s.backgroundColor);
      if (c[3] > 0) layers.push(c);
      if (c[3] === 1) break;
    }
    return layers.reverse().reduce((under, top) => over(top, under), [255, 255, 255]);
  }
  const name = (el) =>
    (el.getAttribute("aria-label") ?? "").trim() ||
    (el.getAttribute("aria-labelledby") ?? "").split(/\s+/).map((id) => document.getElementById(id)?.textContent ?? "").join(" ").trim() ||
    [...(el.labels ?? [])].map((l) => l.textContent).join(" ").trim() ||
    (el.getAttribute("title") ?? "").trim() ||
    (["input", "select", "textarea"].includes(el.tagName.toLowerCase()) ? (el.getAttribute("placeholder") ?? "").trim() : (el.innerText ?? el.textContent ?? "").trim()) ||
    [...el.querySelectorAll("img[alt], svg[aria-label]")].map((i) => i.getAttribute("alt") ?? i.getAttribute("aria-label")).join(" ").trim();

  const controls = [...document.querySelectorAll('a[href], button, input:not([type=hidden]), select, textarea, [role=button], [role=link], [role=tab], [role=menuitem], [role=checkbox], [role=switch], [role=combobox]')].filter(visible);
  const unnamed = controls.filter((el) => name(el) === "").map(label);

  const contrast = [];
  const unjudged = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, { acceptNode: (t) => (t.textContent.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT) });
  const seen = new Set();
  for (let t = walker.nextNode(); t; t = walker.nextNode()) {
    const el = t.parentElement;
    // An inactive control is exempt from 1.4.3 (WCAG: "part of an inactive user interface component").
    if (!el || seen.has(el) || !visible(el) || el.closest("[aria-hidden=true], :disabled, [aria-disabled=true]")) continue;
    seen.add(el);
    const s = getComputedStyle(el);
    const bg = backdrop(el);
    if (bg === null) {
      unjudged.push(label(el));
      continue;
    }
    const fg = over(rgba(s.color), bg);
    const [hi, lo] = [lum(fg), lum(bg)].sort((a, b) => b - a);
    const ratio = (hi + 0.05) / (lo + 0.05);
    const px = parseFloat(s.fontSize);
    const large = px >= 24 || (px >= 18.66 && Number(s.fontWeight) >= 700);
    const need = large ? 3 : 4.5;
    if (ratio < need) contrast.push(`${label(el)} ${ratio.toFixed(2)}:1 < ${need}:1`);
  }

  // Motion = movement (transform/translate/scale/rotate/offsets/size); a colour or opacity change is not.
  const MOVES = /^(transform|translate|scale|rotate|top|left|right|bottom|inset|margin|width|height|all)/;
  const exempt = (el) => !el || el.closest("[role=status], [aria-busy=true], [aria-hidden=true]") !== null;
  const keyframes = (name) => {
    const props = new Set();
    for (const sheet of document.styleSheets) {
      let rules = [];
      try {
        rules = [...sheet.cssRules];
      } catch {
        continue; // a cross-origin sheet: not readable, not ours
      }
      for (const rule of rules)
        if (rule instanceof CSSKeyframesRule && rule.name === name)
          for (const frame of rule.cssRules) for (const prop of frame.style) props.add(prop);
    }
    return [...props];
  };
  const seconds = (list) => Math.max(0, ...list.split(",").map((v) => parseFloat(v) * (v.trim().endsWith("ms") ? 0.001 : 1)));
  const motion = [];
  // Running now (an opening overlay): every CSS animation or transition that moves something.
  for (const a of document.getAnimations()) {
    const el = a.effect?.target;
    const ms = Number(a.effect?.getComputedTiming().duration ?? 0);
    const props = a.transitionProperty ? [a.transitionProperty] : a.effect.getKeyframes().flatMap((k) => Object.keys(k));
    const moving = props.filter((p) => p !== "all" && MOVES.test(p.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)));
    if (!exempt(el) && el instanceof Element && boxed(el) && ms > 10 && moving.length > 0) motion.push(`${label(el)} runs ${a.animationName ?? a.transitionProperty} ${ms}ms (${moving.join(", ")})`);
  }
  // Declared (finished or yet to run): an element whose animation's keyframes move it.
  for (const el of document.querySelectorAll("*")) {
    if (exempt(el) || !boxed(el)) continue;
    const s = getComputedStyle(el);
    if (s.animationName === "none" || seconds(s.animationDuration) <= 0.01) continue;
    const moving = s.animationName.split(",").flatMap((n) => keyframes(n.trim())).filter((p) => p !== "all" && MOVES.test(p));
    if (moving.length > 0) motion.push(`${label(el)} declares ${s.animationName} ${s.animationDuration} (${[...new Set(moving)].join(", ")})`);
  }

  return { overflow: document.documentElement.scrollWidth > window.innerWidth, unnamed, contrast, unjudged, motion };
})()
