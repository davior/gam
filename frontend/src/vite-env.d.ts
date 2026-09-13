/// <reference types="vite/client" />

/**
 * The environment variables this app reads, declared so a typo is a compile error
 * rather than a silent `undefined` at runtime.
 *
 * Both are optional: unset, the app talks to a same-origin `/api` and points sign-in
 * at the production Notes instance, which is what the built image should do.
 */
interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string
  readonly VITE_NOTES_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
