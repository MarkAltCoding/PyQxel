// Writes public/third-party-licenses.txt: the license of every open-source package the
// website and the app ship, which their licenses ask to be passed on with them (MIT,
// BSD, ISC, and Apache require the notice; Pyodide's MPL-2.0 asks that people be told
// where its source is). The legal pages link to it.
//
// The npm packages are every production dependency, found by `npm query`, each with
// the license file it ships. The Python packages and the compiler that come inside
// Pyodide's and YoWASP's files are listed by hand below, with where their source is.

import { execFileSync } from 'node:child_process'
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { writeFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const web = join(dirname(fileURLToPath(import.meta.url)), '..')
const target = join(web, 'public', 'third-party-licenses.txt')

/** What runs inside the vendored Pyodide and Clang files, which npm does not list. */
const BUNDLED = [
  ['CPython (in Pyodide)', 'PSF-2.0', 'https://github.com/python/cpython'],
  ['NumPy (in Pyodide)', 'BSD-3-Clause', 'https://github.com/numpy/numpy'],
  ['pandas (in Pyodide)', 'BSD-3-Clause', 'https://github.com/pandas-dev/pandas'],
  [
    'python-dateutil (in Pyodide)',
    'Apache-2.0 OR BSD-3-Clause',
    'https://github.com/dateutil/dateutil',
  ],
  ['pytz (in Pyodide)', 'MIT', 'https://github.com/stub42/pytz'],
  ['six (in Pyodide)', 'MIT', 'https://github.com/benjaminp/six'],
  ['tzdata (in Pyodide)', 'Apache-2.0', 'https://github.com/python/tzdata'],
  [
    'LLVM and Clang (in YoWASP Clang)',
    'Apache-2.0 WITH LLVM-exception',
    'https://github.com/llvm/llvm-project',
  ],
  ['Pyodide', 'MPL-2.0', 'https://github.com/pyodide/pyodide (unmodified)'],
]

const LICENSE_FILE = /^(licen[cs]e|copying|notice)(\.|-|$)/i

/** The text of every license and notice file at the top of a package, joined. */
function licenseText(path) {
  if (!path || !existsSync(path)) return ''
  return readdirSync(path)
    .filter((name) => LICENSE_FILE.test(name))
    .sort()
    .map((name) => readFileSync(join(path, name), 'utf8').trim())
    .join('\n\n')
}

const packages = JSON.parse(
  execFileSync('npm', ['query', '.prod'], { cwd: web, encoding: 'utf8', maxBuffer: 64 << 20 }),
)
  .filter((entry) => entry.name && entry.path !== web && !entry.private)
  .sort((a, b) => a.name.localeCompare(b.name))

const seen = new Set()
const sections = []
for (const entry of packages) {
  const id = `${entry.name}@${entry.version}`
  if (seen.has(id)) continue
  seen.add(id)
  const license =
    typeof entry.license === 'string' ? entry.license : (entry.license?.type ?? 'see text')
  const text = licenseText(entry.path) || `Licensed under ${license}.`
  sections.push(`${id} (${license})\n${'-'.repeat(id.length)}\n${text}`)
}

const header = [
  'Open-source software in PyQxel',
  '==============================',
  '',
  "PyQxel is built with the open-source software below, used under each one's license.",
  'Its own code is not open source; see the terms of service.',
  '',
  'Bundled runtimes, with where their source is:',
  ...BUNDLED.map(([name, license, source]) => `- ${name}: ${license}, ${source}`),
  '',
  'Pyodide is used unmodified under the Mozilla Public License 2.0; its source is at the',
  'address above, and the license at https://mozilla.org/MPL/2.0/.',
  '',
  'Packages from npm, each with its license:',
  '',
].join('\n')

await writeFile(target, `${header}\n${sections.join('\n\n\n')}\n`)
console.log(`Wrote the licenses of ${sections.length} packages to public/third-party-licenses.txt.`)
