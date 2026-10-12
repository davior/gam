import client from '@/api/client'

/**
 * Disk space: what the library takes up, what the server has left, and the stray files
 * nothing refers to.
 *
 * Everyone gets their own library's share. The server half — the disk, every user's
 * total, the sweep — is for administrators, and comes back null for anyone else.
 */

interface DataResponse<T> {
  data: T
}

/** `ok`, then `low` (the header warns), then `critical` (new writes are refused). */
export type StorageLevel = 'ok' | 'low' | 'critical'

export type VolumeRole = 'media' | 'database' | 'working'

export interface FileGroup {
  files: number
  bytes: number
}

export interface TypeUsage {
  asset_type: string
  files: number
  bytes: number
}

export interface LargestAsset {
  id: string
  name: string
  asset_type: string
  size_bytes: number
}

export interface LibraryUsage {
  total_bytes: number
  files: number
  by_type: TypeUsage[]
  thumbnails: FileGroup
  largest: LargestAsset[]
}

export interface Volume {
  roles: VolumeRole[]
  total_bytes: number
  /**
   * `used + free` is usually short of `total`: ext4 keeps ~5% back for root. That gap is
   * neither used by anything here nor free to write, and is shown as "reserved".
   */
  used_bytes: number
  free_bytes: number
  level: StorageLevel
  /** False for a volume holding only temp space, whose level does not count. */
  guarded: boolean
}

export interface ServerUsage {
  volumes: Volume[]
  /** Every file under the media root: all users, thumbnails and strays included. */
  media: FileGroup
  /** null when the database is not a local SQLite file. */
  database_bytes: number | null
  stray: FileGroup
  warn_free_bytes: number
  reserve_bytes: number
}

export interface StorageSummary {
  /** Everyone's, unlike the numbers behind it. */
  level: StorageLevel
  library: LibraryUsage
  server: ServerUsage | null
}

export interface StorageStatus {
  level: StorageLevel
  /** Admins only; null for everyone else. */
  free_bytes: number | null
  total_bytes: number | null
}

export interface CleanupResult {
  removed: FileGroup
  skipped: number
}

export const storageApi = {
  summary(): Promise<StorageSummary> {
    return client.get<DataResponse<StorageSummary>>('/storage').then((r) => r.data.data)
  },

  status(): Promise<StorageStatus> {
    return client
      .get<DataResponse<StorageStatus>>('/storage/status')
      .then((r) => r.data.data)
  },

  cleanup(): Promise<CleanupResult> {
    return client
      .post<DataResponse<CleanupResult>>('/storage/cleanup')
      .then((r) => r.data.data)
  },
}
