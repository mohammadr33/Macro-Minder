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

  const videoRef = useRef(null)
  const readerRef = useRef(null)
  const isHandlingScanRef = useRef(false)
  const fileInputRef = useRef(null)
  const controlsRef = useRef(null)   // holds the ZXing stream controls object

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
    try {
      controlsRef.current?.stop()
    } catch {}
    controlsRef.current = null

    // Belt-and-suspenders: kill raw tracks on the video element too
    try {
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

  useEffect(() => {
    let mounted = true

    async function startScanner() {
      try {
        const reader = new BrowserMultiFormatReader(hints, {
          delayBetweenScanAttempts: 50,
          delayBetweenScanSuccess: 500,
        })
        readerRef.current = reader

        // Enumerate cameras; prefer back-facing on mobile
        const devices = await BrowserMultiFormatReader.listVideoInputDevices()
        let deviceId = undefined
        if (devices.length > 0) {
          // Pick back camera if label suggests it; otherwise undefined = browser default
          const back = devices.find((d) =>
            /back|rear|environment/i.test(d.label)
          )
          deviceId = back?.deviceId ?? devices[0].deviceId
        }

        const controls = await reader.decodeFromVideoDevice(
          deviceId,
          videoRef.current,
          (result, err, ctrl) => {
            if (!mounted) return
            if (result) {
              handleSuccess(result.getText())
            }
            // NotFoundException is thrown every frame when no barcode found — ignore silently
            if (err && !(err instanceof NotFoundException)) {
              console.warn('ZXing scan error:', err)
            }
          }
        )

        if (!mounted) {
          controls.stop()
          return
        }

        controlsRef.current = controls
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
          setError('Unable to access camera. You can still upload a photo of the barcode below.')
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
    // 1. Native BarcodeDetector (Chrome/Edge) — fastest, most reliable
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

    // 2. ZXing @zxing/browser decodeFromImageUrl — clean ESM, no UMD issues
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
      setFileError('Could not detect a barcode in that image. Try a sharper, closer photo or enter the number below.')
    } finally {
      setIsFileScanning(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  // ─── UI ───────────────────────────────────────────────────────────────────
  return (
    <div className="scanner-modal-backdrop" role="dialog" aria-modal="true" aria-label="Barcode Camera Scanner">
      <div className="scanner-modal-card">
        <div className="scanner-modal-header">
          <div className="scanner-title-row">
            <span className="scanner-pulse-dot" aria-hidden="true" />
            <h3>Scan Product Barcode</h3>
          </div>
          <button className="scanner-close-btn" onClick={handleClose} aria-label="Close camera scanner">
            ✕
          </button>
        </div>

        <div className="scanner-viewport-wrapper">
          {/* @zxing/browser drives this <video> element directly */}
          <video
            ref={videoRef}
            className="scanner-video-el"
            muted
            playsInline
            style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block', borderRadius: '12px' }}
          />

          {isStarting && (
            <div className="scanner-status-overlay">
              <div className="scanner-spinner" />
              <p>Activating camera…</p>
            </div>
          )}

          {isFileScanning && (
            <div className="scanner-status-overlay">
              <div className="scanner-spinner" />
              <p>Analyzing barcode image…</p>
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

        <div className="scanner-modal-footer">
          <div className="scanner-tip-card">
            <strong>💡 Scanning Tips:</strong>
            <ul>
              <li><strong>Focus distance:</strong> Hold package <strong>8–10 inches away</strong> (too close blurs the lens).</li>
              <li><strong>Lighting:</strong> Avoid glare or reflections across the barcode bars.</li>
              <li><strong>Orientation:</strong> Hold barcode horizontally inside the laser zone.</li>
            </ul>
          </div>

          {fileError && <p className="scanner-file-error" role="alert">{fileError}</p>}

          <div className="scanner-actions-row">
            <input
              type="file"
              ref={fileInputRef}
              accept="image/*"
              style={{ display: 'none' }}
              onChange={handleFileUpload}
            />
            <button
              type="button"
              className="scanner-upload-btn"
              onClick={() => fileInputRef.current?.click()}
              disabled={isFileScanning}
            >
              📷 Upload Barcode Photo / Image
            </button>
            <button type="button" className="ghost-btn scanner-cancel-action" onClick={handleClose}>
              Enter Barcode Number Manually
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
