// Puts the Pyodide runtime and the packages custom Python strategies use (numpy and
// pandas, with their dependencies) in public/pyodide/, so the app serves them itself
// and nothing loads from a CDN at runtime: an App Store build must bundle every file it
// runs, and strategies keep working offline.
//
// The runtime comes from the pinned `pyodide` npm package. The wheels are committed in
// vendor/pyodide/<version>/, so builds, Cloud Build's included, need no CDN. A wheel the
// folder lacks, as after upgrading Pyodide, is downloaded from Pyodide's release
// (PYODIDE_PACKAGES_URL overrides where) into that folder, to be committed. Every wheel
// is checked against the sha256 in pyodide-lock.json.
//
// With PYODIDE_OFFLINE=1, as in the Docker build and CI, a missing wheel fails the build
// instead of being downloaded.

import { createHash } from 'node:crypto'
import { existsSync } from 'node:fs'
import { copyFile, mkdir, readFile, rm, writeFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const web = join(dirname(fileURLToPath(import.meta.url)), '..')
const runtime = join(web, 'node_modules', 'pyodide')
const target = join(web, 'public', 'pyodide')

/** Packages strategies can import; their dependencies come with them. */
const PACKAGES = ['numpy', 'pandas']
/** The runtime files `loadPyodide` fetches from its index URL. */
const CORE = [
  'pyodide.mjs',
  'pyodide.asm.mjs',
  'pyodide.asm.wasm',
  'python_stdlib.zip',
  'pyodide-lock.json',
]

const { version } = JSON.parse(await readFile(join(runtime, 'package.json'), 'utf8'))
const lock = JSON.parse(await readFile(join(runtime, 'pyodide-lock.json'), 'utf8'))
const source =
  process.env.PYODIDE_PACKAGES_URL ?? `https://cdn.jsdelivr.net/pyodide/v${version}/full/`
const vendored = join(web, 'vendor', 'pyodide', version)
const offline = process.env.PYODIDE_OFFLINE === '1'

/** `names` and everything they depend on, from the lock file. */
function withDependencies(names) {
  const found = new Set()
  const visit = (name) => {
    const entry = lock.packages[name]
    if (!entry) throw new Error(`Pyodide ${version} has no package named ${name}.`)
    if (found.has(name)) return
    found.add(name)
    for (const dependency of entry.depends ?? []) visit(dependency)
  }
  names.forEach(visit)
  return [...found].sort()
}

const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex')

/** The wheel's bytes, committed or else downloaded, checked against the lock file. */
async function wheel(entry) {
  const committed = join(vendored, entry.file_name)
  if (existsSync(committed)) {
    const bytes = await readFile(committed)
    if (sha256(bytes) === entry.sha256) return bytes
    if (offline) {
      throw new Error(`${committed} does not match the sha256 in pyodide-lock.json.`)
    }
  } else if (offline) {
    throw new Error(
      `${entry.file_name} is not in web/vendor/pyodide/${version}/. Run \`npm run pyodide\` ` +
        'in web/ to download it there, and commit it.',
    )
  }
  const response = await fetch(new URL(entry.file_name, source))
  if (!response.ok) {
    throw new Error(
      `Downloading ${entry.file_name} failed: ${response.status} ${response.statusText}`,
    )
  }
  const bytes = Buffer.from(await response.arrayBuffer())
  if (sha256(bytes) !== entry.sha256) {
    throw new Error(`${entry.file_name} does not match the sha256 in pyodide-lock.json.`)
  }
  await mkdir(vendored, { recursive: true })
  await writeFile(committed, bytes)
  console.log(`Downloaded ${entry.file_name} into vendor/pyodide/${version}/; commit it.`)
  return bytes
}

await rm(target, { recursive: true, force: true })
await mkdir(target, { recursive: true })
for (const file of CORE) await copyFile(join(runtime, file), join(target, file))
const packages = withDependencies(PACKAGES)
let bytes = 0
for (const name of packages) {
  const entry = lock.packages[name]
  const contents = await wheel(entry)
  await writeFile(join(target, entry.file_name), contents)
  bytes += contents.length
}
console.log(
  `Pyodide ${version} with ${packages.join(', ')} (${(bytes / 1e6).toFixed(1)} MB of wheels) ` +
    `is in public/pyodide/.`,
)
