import { useCallback, useEffect, useRef, useState } from 'react'
import { connectLiveDictation, type LiveDictationHandle } from '@/api/suiteSpeech'

/**
 * Dictation into a text box, live: words show as they are heard (`interim`) and each finished
 * phrase is handed to `onFinal` to append. Ported from Gecko Notes' `useDictation`, its
 * Deepgram streaming path only, which is the one that works in every browser (Firefox has
 * no built-in speech recognition) and the one Notes uses there.
 */

export type DictationStatus = 'idle' | 'recording' | 'finishing' | 'error'

export function useDictation(onFinal: (text: string) => void) {
  const [status, setStatus] = useState<DictationStatus>('idle')
  const [interim, setInterim] = useState('')
  const [error, setError] = useState('')
  const handle = useRef<LiveDictationHandle | null>(null)
  const recorder = useRef<MediaRecorder | null>(null)
  const userStopped = useRef(false)
  const onFinalRef = useRef(onFinal)

  useEffect(() => {
    onFinalRef.current = onFinal
  })

  useEffect(
    () => () => {
      handle.current?.close()
      if (recorder.current?.state === 'recording') recorder.current.stop()
    },
    []
  )

  const start = useCallback(async () => {
    userStopped.current = false
    setError('')
    setInterim('')
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch {
      setError('Microphone access was refused.')
      setStatus('error')
      return
    }
    const mimeType = MediaRecorder.isTypeSupported('audio/webm')
      ? 'audio/webm'
      : 'audio/ogg'
    const rec = new MediaRecorder(stream, { mimeType })
    const live = connectLiveDictation(
      (event) => {
        if (event.type === 'interim') setInterim(event.text)
        else if (event.type === 'final') {
          setInterim('')
          if (event.text.trim()) onFinalRef.current(event.text.trim())
        } else {
          setError(event.message)
          setStatus('error')
        }
      },
      (_code, reason) => {
        handle.current = null
        if (rec.state === 'recording') rec.stop()
        setInterim('')
        if (userStopped.current) {
          setStatus((s) => (s === 'error' ? s : 'idle'))
        } else {
          // Ended without the person asking: say so rather than going quietly idle.
          setError(reason || 'The dictation connection was lost.')
          setStatus('error')
        }
      }
    )
    handle.current = live
    rec.ondataavailable = (e) => {
      if (e.data.size > 0) live.sendAudioChunk(e.data)
    }
    rec.onstop = () => {
      stream.getTracks().forEach((t) => t.stop())
      // After the recorder's last chunk (dataavailable fires before stop), so the end of
      // the last sentence reaches Deepgram before it is asked to flush.
      if (userStopped.current) live.stop()
    }
    recorder.current = rec
    rec.start(250)
    setStatus('recording')
  }, [])

  const stop = useCallback(() => {
    if (!handle.current) return
    userStopped.current = true
    setStatus('finishing')
    // The stop itself is sent from the recorder's onstop, after its last chunk.
    if (recorder.current?.state === 'recording') recorder.current.stop()
    else handle.current.stop()
  }, [])

  const toggle = useCallback(() => {
    if (status === 'recording') stop()
    else if (status === 'idle' || status === 'error') void start()
  }, [status, start, stop])

  return { status, interim, error, start, stop, toggle }
}
