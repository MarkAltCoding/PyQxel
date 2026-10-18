interface ImportMetaEnv {
  /** Where the app's copy of Pyodide is served from; defaults to `pyodide/` under the base
   * URL. A native app build can point it at the files it bundles. */
  readonly VITE_PYODIDE_URL?: string
}
