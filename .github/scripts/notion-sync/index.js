#!/usr/bin/env node
/**
 * Syncs docs/**.md changes to Notion pages.
 *
 * - Reads the file->Notion-page-id map from <repo root>/.notion-sync-map.json
 * - For each changed .md file (git diff against the previous pushed commit):
 *     - if mapped: replace that page's content with the file's content
 *     - if not mapped: create a new page under the configured parent
 *       (chosen by matching the longest path prefix in NOTION_DEFAULT_PARENTS)
 *       and add it to the map
 * - Deleted .md files are left untouched in Notion (no auto-delete) — the
 *   map entry stays, so if the file reappears it updates the same page.
 * - If the map changed, commits and pushes it back with [skip ci].
 *
 * Required env:
 *   NOTION_TOKEN            - Notion internal integration secret
 *   NOTION_DEFAULT_PARENTS  - JSON object: { "docs/concepts/": "<page-id>", "docs/": "<page-id>" }
 * Optional env:
 *   GITHUB_EVENT_BEFORE     - commit SHA before this push (falls back to HEAD~1)
 */
const fs = require("fs");
const path = require("path");
const { execSync } = require("child_process");
const { Client } = require("@notionhq/client");
const { markdownToBlocks } = require("@tryfabric/martian");

const REPO_ROOT = process.cwd();
const MAP_PATH = path.join(REPO_ROOT, ".notion-sync-map.json");
const APPEND_CHUNK = 90; // Notion caps append calls at 100 children

const notion = new Client({ auth: requireEnv("NOTION_TOKEN") });
const DEFAULT_PARENTS = JSON.parse(requireEnv("NOTION_DEFAULT_PARENTS"));

function requireEnv(name) {
  const v = process.env[name];
  if (!v) throw new Error(`Missing required env var: ${name}`);
  return v;
}

function loadMap() {
  if (!fs.existsSync(MAP_PATH)) return {};
  return JSON.parse(fs.readFileSync(MAP_PATH, "utf8"));
}

function saveMap(map) {
  const sorted = Object.fromEntries(Object.entries(map).sort(([a], [b]) => a.localeCompare(b)));
  fs.writeFileSync(MAP_PATH, JSON.stringify(sorted, null, 2) + "\n");
}

function diffBase() {
  const before = process.env.GITHUB_EVENT_BEFORE;
  if (before && !/^0+$/.test(before)) {
    try {
      execSync(`git cat-file -e ${before}`, { stdio: "ignore" });
      return before;
    } catch {
      // shallow checkout or force-push edge case; fall through
    }
  }
  return execSync("git rev-parse HEAD~1").toString().trim();
}

function changedMarkdownFiles() {
  const base = diffBase();
  const out = execSync(`git diff --name-status ${base} HEAD -- "docs/**/*.md"`).toString();
  return out
    .split("\n")
    .filter(Boolean)
    .map((line) => {
      const [status, file] = line.split("\t");
      return { status, file };
    });
}

function parentForNewPage(file) {
  const prefixes = Object.keys(DEFAULT_PARENTS).sort((a, b) => b.length - a.length);
  const hit = prefixes.find((p) => file.startsWith(p));
  if (!hit) throw new Error(`No NOTION_DEFAULT_PARENTS entry matches "${file}"`);
  return DEFAULT_PARENTS[hit];
}

function splitTitleAndBody(raw) {
  const match = raw.match(/^#\s+(.+?)\s*\n+/);
  if (match) return { title: match[1].trim(), body: raw.slice(match[0].length) };
  return { title: null, body: raw };
}

async function clearChildren(pageId) {
  let cursor;
  do {
    const res = await notion.blocks.children.list({ block_id: pageId, start_cursor: cursor, page_size: 100 });
    for (const block of res.results) {
      await notion.blocks.delete({ block_id: block.id });
    }
    cursor = res.has_more ? res.next_cursor : undefined;
  } while (cursor);
}

async function appendBlocks(pageId, blocks) {
  for (let i = 0; i < blocks.length; i += APPEND_CHUNK) {
    await notion.blocks.children.append({ block_id: pageId, children: blocks.slice(i, i + APPEND_CHUNK) });
  }
}

async function syncFile(file, map) {
  const raw = fs.readFileSync(path.join(REPO_ROOT, file), "utf8");
  const { title, body } = splitTitleAndBody(raw);
  const pageTitle = title || path.basename(file, ".md");
  const blocks = markdownToBlocks(body);

  let pageId = map[file];
  if (!pageId) {
    const parent = parentForNewPage(file);
    const page = await notion.pages.create({
      parent: { page_id: parent },
      properties: { title: { title: [{ text: { content: pageTitle } }] } },
    });
    pageId = page.id;
    map[file] = pageId;
    console.log(`created ${file} -> ${pageId}`);
  } else {
    await notion.pages.update({
      page_id: pageId,
      properties: { title: { title: [{ text: { content: pageTitle } }] } },
    });
    await clearChildren(pageId);
    console.log(`updating ${file} -> ${pageId}`);
  }

  await appendBlocks(pageId, blocks);
}

function commitMapIfChanged(changed) {
  if (!changed) return;
  execSync('git config user.name "notion-doc-sync"');
  execSync('git config user.email "actions@users.noreply.github.com"');
  execSync(`git add ${JSON.stringify(MAP_PATH)}`);
  try {
    execSync('git commit -m "chore: update notion sync map [skip ci]"');
    execSync("git push");
  } catch (err) {
    console.log("nothing to commit or push failed:", err.message);
  }
}

async function main() {
  const map = loadMap();
  const changes = changedMarkdownFiles();
  if (changes.length === 0) {
    console.log("no docs/**.md changes in this push");
    return;
  }

  let mapChanged = false;
  for (const { status, file } of changes) {
    if (status === "D") {
      console.log(`skipping deleted file (Notion page left as-is): ${file}`);
      continue;
    }
    const before = map[file];
    await syncFile(file, map);
    if (map[file] !== before) mapChanged = true;
  }

  commitMapIfChanged(mapChanged);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
