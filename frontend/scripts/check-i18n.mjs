import { readFile } from 'node:fs/promises';

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
console.log(`i18n keys aligned: ${languages.join(', ')} (${expected.size} keys)`);

