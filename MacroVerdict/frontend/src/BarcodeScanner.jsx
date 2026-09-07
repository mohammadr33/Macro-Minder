import { useEffect, useRef, useState } from 'react'
import { BrowserMultiFormatReader } from '@zxing/browser'
import {
  DecodeHintType,
  BarcodeFormat,
  NotFoundException,
} from '@zxing/library'

export default function BarcodeScanner({ onScan, onClose }) {
  const [error, setError] = useState(null)
  const [isStarting, setIsStarting] = useState(true)
  const [isFileScanning, setIsFileScanning] = useState(false)
  const [fileError, setFileError] = useState(null)
  const [focusRing, setFocusRing] = useState(null)

  // Camera app features
  const [zoomCap, setZoomCap] = useState(null) // { min, max, step }
  const [zoomLevel, setZoomLevel] = useState(1)
  const [hasTorch, setHasTorch] = useState(false)
  const [isTorchOn, setIsTorchOn] = useState(false)
  const [isShutterFlashing, setIsShutterFlashing] = useState(false)

  const videoRef = useRef(null)
  const readerRef = useRef(null)
  const isHandlingScanRef = useRef(false)
  const streamRef = useRef(null)
  const nativeScanIntervalRef = useRef(null)
  const fileInputRef = useRef(null)
  const cameraAppInputRef = useRef(null)
  const controlsRef = useRef(null)

  // Build hints once
  const hints = new Map()
  hints.set(DecodeHintType.POSSIBLE_FORMATS, [
    BarcodeFormat.UPC_A,
    BarcodeFormat.UPC_E,
    BarcodeFormat.EAN_13,
    BarcodeFormat.EAN_8,
    BarcodeFormat.CODE_128,
    BarcodeFormat.CODE_39,
    BarcodeFormat.ITF,
    BarcodeFormat.QR_CODE,
    BarcodeFormat.DATA_MATRIX,
  ])
  hints.set(DecodeHintType.TRY_HARDER, true)

  const stopCamera = () => {
    if (nativeScanIntervalRef.current) {
      clearInterval(nativeScanIntervalRef.current)
      nativeScanIntervalRef.current = null
    }

    try {
      controlsRef.current?.stop()
    } catch {}
    controlsRef.current = null

    try {
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((t) => { try { t.stop() } catch {} })
        streamRef.current = null
      }
      const stream = videoRef.current?.srcObject
      if (stream) {
        stream.getTracks().forEach((t) => { try { t.stop() } catch {} })
        videoRef.current.srcObject = null
      }
    } catch {}
  }

  const handleClose = () => {
    stopCamera()
    onClose()
  }

  const handleSuccess = (text) => {
    if (isHandlingScanRef.current) return
    isHandlingScanRef.current = true
    stopCamera()
    onScan(text)
  }

  // Tap-to-focus on viewfinder
  const handleViewportClick = (e) => {
    try {
      const rect = e.currentTarget?.getBoundingClientRect()
      if (!rect) return

      const clientX = e.clientX ?? (e.touches && e.touches[0] ? e.touches[0].clientX : null)
      const clientY = e.clientY ?? (e.touches && e.touches[0] ? e.touches[0].clientY : null)

      const x = typeof clientX === 'number' && !Number.isNaN(clientX) ? clientX - rect.left : rect.width / 2
      const y = typeof clientY === 'number' && !Number.isNaN(clientY) ? clientY - rect.top : rect.height / 2

      const tapId = Date.now()
      setFocusRing({ x, y, id: tapId })
      setTimeout(() => {
        setFocusRing((curr) => (curr?.id === tapId ? null : curr))
      }, 1200)

      // Hardware camera autofocus adjustment (strictly guarded)
      const stream = streamRef.current || videoRef.current?.srcObject
      if (stream && typeof stream.getVideoTracks === 'function') {
        const tracks = stream.getVideoTracks()
        if (tracks && tracks.length > 0) {
          const track = tracks[0]
          if (track && track.readyState === 'live' && typeof track.applyConstraints === 'function') {
            const caps = typeof track.getCapabilities === 'function' ? track.getCapabilities() : {}
            const advanced = {}

            if (caps.focusMode && Array.isArray(caps.focusMode)) {
              if (caps.focusMode.includes('single-shot')) {
                advanced.focusMode = 'single-shot'
              } else if (caps.focusMode.includes('continuous')) {
                advanced.focusMode = 'continuous'
              }
            }

            if (caps.pointsOfInterest) {
              const relX = Math.max(0, Math.min(1, x / rect.width))
              const relY = Math.max(0, Math.min(1, y / rect.height))
              advanced.pointsOfInterest = [{ x: relX, y: relY }]
            }

            if (Object.keys(advanced).length > 0) {
              track.applyConstraints({ advanced: [advanced] })
                .then(() => {
                  if (advanced.focusMode === 'single-shot' && caps.focusMode.includes('continuous')) {
                    setTimeout(() => {
                      try {
                        track.applyConstraints({ advanced: [{ focusMode: 'continuous' }] }).catch(() => {})
                      } catch {}
                    }, 800)
                  }
                })
                .catch(() => {})
            }
          }
        }
      }
    } catch {}
  }

  // Quick Zoom toggle (1x / 2x)
  const handleZoomToggle = (e) => {
    e?.stopPropagation()
    const stream = streamRef.current || videoRef.current?.srcObject
    const track = stream?.getVideoTracks?.()[0]
    if (!track || !zoomCap) return

    const targetZoom = zoomLevel === 1 ? Math.min(2, zoomCap.max) : 1
    track.applyConstraints({ advanced: [{ zoom: targetZoom }] })
      .then(() => setZoomLevel(targetZoom))
      .catch(() => {})
  }

  // Torch / Flash toggle
  const handleTorchToggle = (e) => {
    e?.stopPropagation()
    const stream = streamRef.current || videoRef.current?.srcObject
    const track = stream?.getVideoTracks?.()[0]
    if (!track) return

    const nextTorch = !isTorchOn
    track.applyConstraints({ advanced: [{ torch: nextTorch }] })
      .then(() => setIsTorchOn(nextTorch))
      .catch(() => {})
  }

  // Shutter Snap from live video
  const handleShutterSnap = async () => {
    if (isHandlingScanRef.current || isFileScanning) return
    const video = videoRef.current
    if (!video || video.readyState < 2) return

    setIsShutterFlashing(true)
    setTimeout(() => setIsShutterFlashing(false), 240)
    setFileError(null)

    try {
      const canvas = document.createElement('canvas')
      canvas.width = video.videoWidth || 1280
      canvas.height = video.videoHeight || 720
      const ctx = canvas.getContext('2d')
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height)

      // 1. Check native BarcodeDetector on canvas snapshot
      if (typeof window !== 'undefined' && 'BarcodeDetector' in window) {
        try {
          const detector = new window.BarcodeDetector({
            formats: ['upc_a', 'upc_e', 'ean_13', 'ean_8', 'code_128', 'code_39', 'itf', 'qr_code'],
          })
          const barcodes = await detector.detect(canvas)
          if (barcodes.length > 0 && barcodes[0].rawValue) {
            handleSuccess(barcodes[0].rawValue)
            return
          }
        } catch {}
      }

      // 2. Check ZXing decode from canvas data URL
      const dataUrl = canvas.toDataURL('image/jpeg', 0.95)
      const reader = new BrowserMultiFormatReader(hints)
      try {
        const result = await reader.decodeFromImageUrl(dataUrl)
        if (result) {
          handleSuccess(result.getText())
          return
        }
      } catch {}

      setFileError('Could not read barcode in that snapshot. Try 2x Zoom, hold 8–10 in. away, or use "Phone Camera App" below!')
    } catch (err) {
      console.error('Shutter frame capture failed:', err)
    }
  }

  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.key === 'Escape') handleClose()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    let mounted = true

    async function startScanner() {
      try {
        const reader = new BrowserMultiFormatReader(hints, {
          delayBetweenScanAttempts: 40,
          delayBetweenScanSuccess: 500,
        })
        readerRef.current = reader

        // Request high-res stream with continuous autofocus
        let stream = null
        try {
          stream = await navigator.mediaDevices.getUserMedia({
            video: {
              facingMode: { ideal: 'environment' },
              width: { ideal: 1920, min: 1280 },
              height: { ideal: 1080, min: 720 },
              advanced: [{ focusMode: 'continuous' }],
            },
            audio: false,
          })
        } catch {
          try {
            stream = await navigator.mediaDevices.getUserMedia({
              video: {
                facingMode: { ideal: 'environment' },
                width: { ideal: 1280 },
                height: { ideal: 720 },
              },
              audio: false,
            })
          } catch {
            stream = await navigator.mediaDevices.getUserMedia({
              video: { facingMode: { ideal: 'environment' } },
              audio: false,
            })
          }
        }

        if (!mounted) {
          stream.getTracks().forEach((t) => t.stop())
          return
        }

        streamRef.current = stream

        if (videoRef.current) {
          videoRef.current.srcObject = stream
          videoRef.current.setAttribute('playsinline', 'true')
          videoRef.current.setAttribute('muted', 'true')
          await videoRef.current.play()
        }

        const track = stream.getVideoTracks()[0]
        if (track) {
          try {
            const caps = typeof track.getCapabilities === 'function' ? track.getCapabilities() : {}
            // Continuous autofocus
            if (caps.focusMode && Array.isArray(caps.focusMode) && caps.focusMode.includes('continuous')) {
              track.applyConstraints({ advanced: [{ focusMode: 'continuous' }] }).catch(() => {})
            }
            // Zoom support
            if (caps.zoom && caps.zoom.max > 1) {
              setZoomCap({ min: caps.zoom.min || 1, max: caps.zoom.max || 1, step: caps.zoom.step || 0.1 })
            }
            // Torch support
            if (caps.torch) {
              setHasTorch(true)
            }
          } catch {}
        }

        // 1. Android Native BarcodeDetector loop (hardware MLKit accelerated)
        if (typeof window !== 'undefined' && 'BarcodeDetector' in window) {
          try {
            const detector = new window.BarcodeDetector({
              formats: ['upc_a', 'upc_e', 'ean_13', 'ean_8', 'code_128', 'code_39', 'itf', 'qr_code'],
            })

            let isDetecting = false
            const interval = setInterval(async () => {
              if (!mounted || isHandlingScanRef.current || isDetecting) return
              const video = videoRef.current
              if (!video || video.readyState < 2 || video.videoWidth === 0) return

              isDetecting = true
              try {
                const barcodes = await detector.detect(video)
                if (barcodes.length > 0 && barcodes[0].rawValue) {
                  clearInterval(interval)
                  handleSuccess(barcodes[0].rawValue)
                }
              } catch {
                // frame detection errors are silent
              } finally {
                isDetecting = false
              }
            }, 80)

            nativeScanIntervalRef.current = interval
          } catch {}
        }

        // 2. ZXing scanner fallback (essential for iOS Safari / desktop Firefox)
        if (videoRef.current) {
          const controls = await reader.decodeFromVideoElement(
            videoRef.current,
            (result, err) => {
              if (!mounted) return
              if (result) {
                handleSuccess(result.getText())
              }
              if (err && !(err instanceof NotFoundException)) {
                console.warn('ZXing scan warning:', err)
              }
            }
          )

          if (!mounted) {
            controls?.stop()
            return
          }
          controlsRef.current = controls
        }

        setIsStarting(false)
      } catch (err) {
        if (!mounted) return
        setIsStarting(false)
        const msg = String(err?.message || err || '')
        if (err?.name === 'NotAllowedError' || msg.includes('ermission')) {
          setError('Camera permission was denied. Please allow camera access in your browser settings.')
        } else if (err?.name === 'NotFoundError' || msg.includes('NotFound')) {
          setError('No camera found on this device.')
        } else {
          setError('Unable to access live camera stream. You can still snap or upload a photo using your phone camera below.')
        }
      }
    }

    startScanner()

    return () => {
      mounted = false
      stopCamera()
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // ─── Image / file upload decoder ──────────────────────────────────────────
  async function decodeBarcodeFromFile(file) {
    // 1. Native BarcodeDetector (Chrome/Android MLKit) — fastest, most reliable
    if (typeof window !== 'undefined' && 'BarcodeDetector' in window) {
      try {
        const detector = new window.BarcodeDetector({
          formats: ['upc_a', 'upc_e', 'ean_13', 'ean_8', 'code_128', 'code_39', 'itf', 'qr_code'],
        })
        const bitmap = await createImageBitmap(file)
        const barcodes = await detector.detect(bitmap)
        if (barcodes.length > 0 && barcodes[0].rawValue) {
          return barcodes[0].rawValue
        }
      } catch {}
    }

    // 2. ZXing @zxing/browser decodeFromImageUrl
    const reader = new BrowserMultiFormatReader(hints)
    const url = URL.createObjectURL(file)

    try {
      const result = await reader.decodeFromImageUrl(url)
      if (result) return result.getText()
    } catch {}

    // 3. Canvas multi-angle fallback (handles rotated/portrait shots)
    const img = await new Promise((resolve, reject) => {
      const el = new Image()
      el.onload = () => resolve(el)
      el.onerror = () => reject(new Error('Image load failed'))
      el.src = url
    })

    const angles = [0, 90, 180, 270]
    for (const angle of angles) {
      const canvas = document.createElement('canvas')
      const isRotated = angle === 90 || angle === 270
      canvas.width  = isRotated ? img.naturalHeight : img.naturalWidth
      canvas.height = isRotated ? img.naturalWidth  : img.naturalHeight
      const ctx = canvas.getContext('2d')
      ctx.translate(canvas.width / 2, canvas.height / 2)
      ctx.rotate((angle * Math.PI) / 180)
      ctx.drawImage(img, -img.naturalWidth / 2, -img.naturalHeight / 2)

      try {
        const dataUrl = canvas.toDataURL('image/png')
        const result  = await reader.decodeFromImageUrl(dataUrl)
        if (result) {
          URL.revokeObjectURL(url)
          return result.getText()
        }
      } catch {}
    }

    URL.revokeObjectURL(url)
    throw new Error('No barcode detected in image')
  }

  async function handleFileUpload(e) {
    const file = e.target.files?.[0]
    if (!file) return

    setFileError(null)
    setIsFileScanning(true)

    try {
      const text = await decodeBarcodeFromFile(file)
      stopCamera()
      onScan(text)
    } catch (err) {
      console.error('Barcode upload failed:', err)
      setFileError('Could not detect a barcode in that photo. Make sure the barcode is well-lit and clear, or enter the number below.')
    } finally {
      setIsFileScanning(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
      if (cameraAppInputRef.current) cameraAppInputRef.current.value = ''
    }
  }

  // ─── UI ───────────────────────────────────────────────────────────────────
  return (
    <div
      className="scanner-modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="Scan food product barcode"
      onClick={handleClose}
    >
      <div
        className="scanner-modal-card"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="scanner-modal-header">
          <div className="scanner-header-title-row">
            <span className="scanner-live-dot" aria-hidden="true" />
            <span className="scanner-modal-title">Live Barcode Scanner</span>
          </div>
          <button
            type="button"
            className="scanner-close-btn"
            onClick={handleClose}
            aria-label="Close camera scanner"
          >
            ✕
          </button>
        </div>

        <div
          className="scanner-viewport-wrapper"
          onClick={handleViewportClick}
          style={{ cursor: 'crosshair' }}
          title="Tap screen to focus"
        >
          {/* Video feed */}
          <video
            ref={videoRef}
            className="scanner-video-el"
            muted
            playsInline
            style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
          />

          {/* Shutter flash effect */}
          {isShutterFlashing && <div className="scanner-shutter-flash" />}

          {/* Quick Camera Controls Bar (Pills in top corner) */}
          <div className="scanner-viewport-controls">
            {zoomCap && (
              <button
                type="button"
                className={`scanner-ctrl-pill ${zoomLevel > 1 ? 'active' : ''}`}
                onClick={handleZoomToggle}
                title="Toggle 1x or 2x Zoom"
              >
                {zoomLevel === 1 ? '1x' : '2x'}
              </button>
            )}
            {hasTorch && (
              <button
                type="button"
                className={`scanner-ctrl-pill ${isTorchOn ? 'active' : ''}`}
                onClick={handleTorchToggle}
                title="Toggle Torch / Flashlight"
              >
                {isTorchOn ? '🔦 ON' : '🔦 OFF'}
              </button>
            )}
          </div>

          {/* Animated Tap-to-Focus Target Box */}
          {focusRing && (
            <div
              className="scanner-focus-ring"
              style={{ left: `${focusRing.x}px`, top: `${focusRing.y}px` }}
              aria-hidden="true"
            >
              <div className="focus-ring-bracket tl" />
              <div className="focus-ring-bracket tr" />
              <div className="focus-ring-bracket bl" />
              <div className="focus-ring-bracket br" />
            </div>
          )}

          {/* Tap-to-focus helper hint */}
          {!isStarting && !error && !isFileScanning && (
            <div className="scanner-tap-hint" aria-hidden="true">
              <span>👆 Tap screen to focus</span>
            </div>
          )}

          {isStarting && (
            <div className="scanner-status-overlay">
              <div className="scanner-spinner" />
              <p>Activating HD camera…</p>
            </div>
          )}

          {isFileScanning && (
            <div className="scanner-status-overlay">
              <div className="scanner-spinner" />
              <p>Analyzing barcode with native engine…</p>
            </div>
          )}

          {error && (
            <div className="scanner-status-overlay scanner-error-overlay">
              <p className="scanner-error-msg">{error}</p>
              <button className="scanner-fallback-btn" onClick={handleClose}>
                Enter Barcode Manually
              </button>
            </div>
          )}

          {!isStarting && !error && !isFileScanning && (
            <div className="scanner-aiming-reticle" aria-hidden="true">
              <div className="reticle-corner top-left" />
              <div className="reticle-corner top-right" />
              <div className="reticle-corner bottom-left" />
              <div className="reticle-corner bottom-right" />
              <div className="reticle-laser-line" />
            </div>
          )}
        </div>

        {/* Shutter capture button bar (one-to-one with camera app) */}
        {!isStarting && !error && (
          <div className="scanner-shutter-bar">
            <button
              type="button"
              className="scanner-shutter-btn"
              onClick={handleShutterSnap}
              title="Snap & Scan current frame"
              aria-label="Snap current barcode frame"
            >
              <div className="shutter-inner" />
            </button>
            <span className="scanner-shutter-label">📸 Tap to snap current frame</span>
          </div>
        )}

        <div className="scanner-modal-footer">
          {fileError && <p className="scanner-file-error" role="alert">{fileError}</p>}

          {/* Hidden inputs: 1 for direct native camera app, 1 for gallery upload */}
          <input
            type="file"
            ref={cameraAppInputRef}
            accept="image/*"
            capture="environment"
            style={{ display: 'none' }}
            onChange={handleFileUpload}
          />
          <input
            type="file"
            ref={fileInputRef}
            accept="image/*"
            style={{ display: 'none' }}
            onChange={handleFileUpload}
          />

          <div className="scanner-actions-grid-v2">
            <button
              type="button"
              className="scanner-action-btn primary-cam-btn"
              onClick={() => cameraAppInputRef.current?.click()}
              disabled={isFileScanning}
              title="Opens your device's native camera app"
            >
              <span className="scanner-action-icon" aria-hidden="true">📱</span>
              <div className="scanner-action-text">
                <strong>Phone Camera App</strong>
                <span className="scanner-action-sub">Full autofocus & macro</span>
              </div>
            </button>

            <button
              type="button"
              className="scanner-action-btn gallery-btn"
              onClick={() => fileInputRef.current?.click()}
              disabled={isFileScanning}
              title="Choose a photo from your gallery"
            >
              <span className="scanner-action-icon" aria-hidden="true">🖼️</span>
              <div className="scanner-action-text">
                <strong>Photo Gallery</strong>
                <span className="scanner-action-sub">Upload existing image</span>
              </div>
            </button>

            <button
              type="button"
              className="scanner-cancel-btn"
              onClick={handleClose}
            >
              ✕ Exit
            </button>
          </div>

          <div className="scanner-tip-card">
            <div className="scanner-focus-alert">
              💡 <strong>Close-up blur on Android?</strong> Hold phone <strong>8–10 in. away</strong> &amp; tap <strong>2x</strong> zoom above, or use <strong>Phone Camera App</strong> for native macro autofocus!
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
