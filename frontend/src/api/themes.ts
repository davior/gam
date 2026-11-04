import axios, { type AxiosInstance } from 'axios'
import { readToken } from '@/api/client'
import { currentConfig } from '@/api/config'

interface DataResponse<T> {
  data: T
}

interface ListResponse<T> {
  data: T[]
  total: number
  limit: number
  offset: number
}

/**
 * Themes and which one this app wears (M7), from Gecko Suite. Copied from the suite's
 * `api/themes.ts`; only the client differs.
 *
 * The browser calls the suite directly, cross-origin, as the same person: GAM's token
 * (the suite verifies the same JWT) and the shared cookie. Its own axios instance, not
 * GAM's client, so a refusal from the suite can never sign anyone out of GAM.
 */

export type ThemeApp = 'notes' | 'gam' | 'gvc' | 'suite'

export interface ThemeValues {
  name: string
  mode: 'light' | 'dark'
  bg_type: 'flat' | 'gradient' | 'image'
  bg_color1: string
  bg_color2: string | null
  bg_image_url: string | null
  bg_image_mode: 'fill' | 'stretch' | 'repeat'
  bg_blur: number
  glass_opacity: number
  glass_blur: number
  shadow_size: number
  shadow_blur: number
}

export interface Theme extends ThemeValues {
  id: string
  scope: 'instance' | 'user'
  user_id: string | null
  /** Whether the caller may change it: their own, or a shared one for an admin. */
  editable: boolean
  created_at: string
  updated_at: string
}

export interface Selection {
  app: ThemeApp
  theme: Theme | null
}

export function makeThemesApi(http: AxiosInstance) {
  return {
    list(): Promise<Theme[]> {
      return http.get<ListResponse<Theme>>('/v1/themes').then((r) => r.data.data)
    },
    create(body: ThemeValues & { scope: 'instance' | 'user' }): Promise<Theme> {
      return http.post<DataResponse<Theme>>('/v1/themes', body).then((r) => r.data.data)
    },
    update(id: string, body: Partial<ThemeValues>): Promise<Theme> {
      return http
        .patch<DataResponse<Theme>>(`/v1/themes/${id}`, body)
        .then((r) => r.data.data)
    },
    remove(id: string): Promise<void> {
      return http.delete(`/v1/themes/${id}`).then(() => undefined)
    },
    /** The image itself as the body; answers with the absolute address to use. */
    uploadImage(file: File): Promise<string> {
      return http
        .post<DataResponse<{ url: string }>>('/v1/themes/images', file, {
          headers: { 'Content-Type': file.type },
        })
        .then((r) => r.data.data.url)
    },
    selected(app: ThemeApp): Promise<Theme | null> {
      return http
        .get<DataResponse<Selection>>(`/v1/themes/selected/${app}`)
        .then((r) => r.data.data.theme)
    },
    choose(app: ThemeApp, themeId: string | null): Promise<Theme | null> {
      return http
        .put<DataResponse<Selection>>(`/v1/themes/selected/${app}`, { theme_id: themeId })
        .then((r) => r.data.data.theme)
    },
  }
}

const suite = axios.create({ withCredentials: true })

suite.interceptors.request.use((config) => {
  // `loadConfig()` has always answered before the theme store asks (it awaits it).
  const base = (currentConfig()?.suite_base_url ?? '').replace(/\/+$/, '')
  config.baseURL = `${base}/api`
  const token = readToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

export const themesApi = makeThemesApi(suite)
