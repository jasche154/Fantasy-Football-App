/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Backend origin, e.g. "http://localhost:8000". */
  readonly VITE_API_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
