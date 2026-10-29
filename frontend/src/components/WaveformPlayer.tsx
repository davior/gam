import { useCallback, useEffect, useRef, useState } from 'react'
import { Pause, Play } from 'lucide-react'
import WaveSurfer from 'wavesurfer.js'
import { assetsApi, type Asset, type Waveform } from '@/api/assets'
import { formatDuration } from '@/utils/format'

/** How far an arrow key moves the playhead. */
const SEEK_STEP_SECONDS = 5

// Fixed rather than themed: the media well behind this is bg-gray-900 in both themes.
const WAVE_COLOR = '#4b5563' // gray-600
const PROGRESS_COLOR = '#60a5fa' // primary-400
const CURSOR_COLOR = '#bfdbfe' // primary-200

interface Props {
  asset: Asset
  attachPlayer: (player: HTMLAudioElement | null) => void
  onTimeUpdate: (e: React.SyntheticEvent<HTMLAudioElement>) => void
}

/**
 * An audio player whose scrub bar is the recording's waveform.
 *
 * The `<audio>` element is still the player — it is only stripped of its native
 * controls. wavesurfer.js is handed it as `media` and draws over it, so everything
 * else that drives or listens to the element (`attachPlayer`'s start-at seek, the
 * transcript's click-to-seek, the clip editor's "set in point here", the clip's
 * out-point pause) keeps working without knowing a waveform exists.
 *
 * wavesurfer is never allowed to make the waveform itself: given no peaks it downloads
 * and decodes the whole file, which for a ninety-minute interview is the whole file
 * before anything draws. It always gets peaks — the server's (`assetsApi.peaks`), or a
 * flat placeholder while those load or if they cannot be had. The placeholder still
 * scrubs, so a long recording opened for the first time is usable at once.
 */
export default function WaveformPlayer({ asset, attachPlayer, onTimeUpdate }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const [audio, setAudio] = useState<HTMLAudioElement | null>(null)
  const [waveform, setWaveform] = useState<Waveform | null>(null)
  const [playing, setPlaying] = useState(false)
  const [time, setTime] = useState(0)
  const [duration, setDuration] = useState(0)

  // Memoised on `attachPlayer` alone. A ref callback that changed every render would be
  // detached and re-attached every render, and `attachPlayer` seeks on attach.
  const ref = useCallback(
    (element: HTMLAudioElement | null) => {
      setAudio(element)
      attachPlayer(element)
    },
    [attachPlayer]
  )

  useEffect(() => {
    let stale = false
    setWaveform(null)
    setPlaying(false)
    assetsApi
      .peaks(asset.id)
      .then((result) => {
        if (!stale) setWaveform(result)
      })
      .catch(() => {
        // The flat placeholder stays. It scrubs just the same; a waveform is a nicety.
      })
    return () => {
      stale = true
    }
  }, [asset.id])

  // Rebuilt, not updated, when the real peaks arrive: `setOptions` replaces the data
  // without redrawing it. Destroying is safe for the element — wavesurfer leaves
  // external media exactly as it found it, so playback carries on through the swap.
  useEffect(() => {
    const container = containerRef.current
    if (!audio || !container) return
    const peaks = waveform?.peaks.length ? waveform.peaks : [0]
    const knownDuration = Number.isFinite(audio.duration) ? audio.duration : undefined
    const surfer = WaveSurfer.create({
      container,
      media: audio,
      peaks: [peaks],
      duration: waveform?.duration ?? knownDuration,
      height: 64,
      waveColor: WAVE_COLOR,
      progressColor: PROGRESS_COLOR,
      cursorColor: CURSOR_COLOR,
      cursorWidth: 1,
      barWidth: 2,
      barGap: 1,
      barRadius: 2,
      dragToSeek: true,
    })
    return () => surfer.destroy()
  }, [audio, waveform])

  const toggle = useCallback(() => {
    if (!audio) return
    if (audio.paused) {
      void audio.play()?.catch(() => {
        // Refused (autoplay policy, a decode error): the button simply stays on Play.
      })
    } else {
      audio.pause()
    }
  }, [audio])

  const seekBy = (delta: number) => {
    if (!audio) return
    const end = duration || audio.duration || 0
    audio.currentTime = Math.min(Math.max(audio.currentTime + delta, 0), end)
  }

  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'ArrowLeft') seekBy(-SEEK_STEP_SECONDS)
    else if (e.key === 'ArrowRight') seekBy(SEEK_STEP_SECONDS)
    else if (e.key === ' ') toggle()
    else return
    e.preventDefault()
  }

  const readDuration = (e: React.SyntheticEvent<HTMLAudioElement>) => {
    const value = e.currentTarget.duration
    if (Number.isFinite(value)) setDuration(value)
  }

  // A clip plays a window of its parent's file, so it is drawn as that window: the
  // parent's whole waveform with everything outside the in/out points dimmed.
  const clipWindow =
    duration > 0 && asset.in_point !== null && asset.out_point !== null
      ? {
          before: (Math.max(asset.in_point, 0) / duration) * 100,
          after: (Math.max(duration - asset.out_point, 0) / duration) * 100,
        }
      : null

  return (
    <div className="flex w-full max-w-2xl items-center gap-3 px-4">
      <audio
        key={asset.id}
        ref={ref}
        src={asset.file_url ?? undefined}
        preload="metadata"
        onTimeUpdate={(e) => {
          setTime(e.currentTarget.currentTime)
          onTimeUpdate(e)
        }}
        onLoadedMetadata={readDuration}
        onDurationChange={readDuration}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
      />

      <button
        type="button"
        onClick={toggle}
        aria-label={playing ? 'Pause' : 'Play'}
        className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary-500 text-white hover:bg-primary-400 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-200"
      >
        {playing ? <Pause className="h-5 w-5" /> : <Play className="ml-0.5 h-5 w-5" />}
      </button>

      <div
        role="slider"
        tabIndex={0}
        aria-label="Seek"
        aria-valuemin={0}
        aria-valuemax={Math.round(duration)}
        aria-valuenow={Math.round(time)}
        aria-valuetext={`${formatDuration(time)} of ${formatDuration(duration)}`}
        onKeyDown={onKeyDown}
        className="relative min-w-0 flex-1 cursor-pointer rounded focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-200"
      >
        {/* Behind the bars, so the placeholder reads as a track rather than as nothing. */}
        <div className="pointer-events-none absolute inset-x-0 top-1/2 h-px bg-gray-700" />
        <div ref={containerRef} data-testid="waveform" />
        {/* z-10: wavesurfer stacks its progress layer and cursor at z-index 2 and 5,
            which would otherwise paint over the shading. */}
        {clipWindow && (
          <>
            <div
              className="pointer-events-none absolute inset-y-0 left-0 z-10 bg-gray-900/70"
              style={{ width: `${clipWindow.before}%` }}
            />
            <div
              className="pointer-events-none absolute inset-y-0 right-0 z-10 bg-gray-900/70"
              style={{ width: `${clipWindow.after}%` }}
            />
          </>
        )}
      </div>

      <span className="shrink-0 font-mono text-xs tabular-nums text-gray-400">
        {formatDuration(time) || '0:00'} / {formatDuration(duration) || '0:00'}
      </span>
    </div>
  )
}
