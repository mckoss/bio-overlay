// The web and desktop apps share one version number; this fails the suite
// (and the Pages deploy, which runs it) whenever the copies drift.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { VERSION } from "../version.js";

const repo = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

test("web VERSION matches the Python package version", () => {
  const init = readFileSync(join(repo, "src", "bio_overlay", "__init__.py"), "utf8");
  assert.equal(VERSION, init.match(/__version__\s*=\s*"([^"]+)"/)[1]);
});

test("web VERSION matches pyproject.toml", () => {
  const pyproject = readFileSync(join(repo, "pyproject.toml"), "utf8");
  assert.equal(VERSION, pyproject.match(/^version\s*=\s*"([^"]+)"/m)[1]);
});

test("VERSION is semver-shaped", () => {
  assert.match(VERSION, /^\d+\.\d+\.\d+$/);
});
