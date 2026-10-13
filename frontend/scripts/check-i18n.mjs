import { readFile, readdir } from 'node:fs/promises';
import { join, extname } from 'node:path';
import { fileURLToPath } from 'node:url';

const locales = JSON.parse(await readFile(new URL('../../docs/ui/locales.json', import.meta.url), 'utf8'));
const languages = Object.keys(locales);
const [first, ...rest] = languages;
const expected = new Set(Object.keys(locales[first] ?? {}));
const errors = [];

for (const language of rest) {
  const actual = new Set(Object.keys(locales[language] ?? {}));
  for (const key of expected) if (!actual.has(key)) errors.push(`${language}: missing ${key}`);
  for (const key of actual) if (!expected.has(key)) errors.push(`${language}: extra ${key}`);
}

if (errors.length) {
  console.error(errors.join('\n'));
  process.exit(1);
}

const referenced = new Set();
async function scan(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) await scan(path);
    if (!entry.isFile() || !['.ts', '.tsx'].includes(extname(entry.name))) continue;
    const source = await readFile(path, 'utf8');
    const pattern = /(?:^|[^A-Za-z0-9_$])(?:[A-Za-z_$][\w$]*\.)?t\(\s*["']([^"']+)["']/g;
    for (const match of source.matchAll(pattern)) referenced.add(match[1]);
  }
}
await scan(fileURLToPath(new URL('../src', import.meta.url)));
for (const key of [...referenced].sort()) {
  if (!expected.has(key) && !key.includes('${')) errors.push(`source: missing ${key}`);
}
if (errors.length) {
  console.error(errors.join('\n'));
  process.exit(1);
}
console.log(`i18n keys aligned: ${languages.join(', ')} (${expected.size} keys)`);

