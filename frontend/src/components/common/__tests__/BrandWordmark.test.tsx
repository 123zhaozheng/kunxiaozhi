import assert from "node:assert/strict";
import { renderToStaticMarkup } from "react-dom/server";
import { test } from "node:test";
import { BrandWordmark } from "../BrandWordmark";

test("BrandWordmark renders an accessible scalable svg by default", () => {
  const html = renderToStaticMarkup(<BrandWordmark className="brand-mark" />);

  assert.match(html, /<svg/);
  assert.match(html, /viewBox="0 0 220 62"/);
  assert.match(html, /role="img"/);
  assert.match(html, /<title[^>]*>昆小智<\/title>/);
  assert.match(html, /aria-labelledby="[^"]+"/);
  assert.match(html, /class="brand-mark"/);
  assert.match(html, /data-wordmark-style="text-only"/);
  assert.match(html, /<text[^>]*>昆小智<\/text>/);
  assert.match(html, /x="110"/);
  assert.match(html, /y="36"/);
  assert.match(html, /text-anchor="middle"/);
  assert.match(html, /dominant-baseline="central"/);
  assert.doesNotMatch(html, /<path/);
  assert.doesNotMatch(html, /<circle/);
});

test("BrandWordmark can render decoratively when the parent already labels it", () => {
  const html = renderToStaticMarkup(<BrandWordmark decorative />);

  assert.match(html, /aria-hidden="true"/);
  assert.doesNotMatch(html, /role="img"/);
  assert.doesNotMatch(html, /<title>/);
});
