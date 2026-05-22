import { useState, useRef, useEffect, useCallback } from 'react'
import { Button, Input, Space } from '@arco-design/web-react'
import { CHATROOM_API_URL, CHATROOM_WIDGET_URL } from '../config'

interface Props {
  chatroomId: string
  /**
   * Called before the preview launches so unsaved form changes don't get
   * lost. Should resolve only on successful save (or reject to abort the
   * launch). Backed by ChatroomEditor.handleSave.
   */
  onSaveBeforeLaunch?: () => Promise<void>
}

/**
 * One mounted preview iframe. Identified by a stable id so postMessage
 * replies can be routed back to the right history pane (we filter by
 * ``MessageEvent.source`` against this iframe's contentWindow).
 */
interface PreviewInstance {
  id: string
  blobUrl: string
  history: string
}

export default function WidgetPreview({ chatroomId, onSaveBeforeLaunch }: Props) {
  const isDev = import.meta.env.DEV
  const [previews, setPreviews] = useState<PreviewInstance[]>([])
  const [launching, setLaunching] = useState(false)
  /** Dev-only override for the chatroom backend hostname. Hidden in prod. */
  const [hostnameOverride, setHostnameOverride] = useState(CHATROOM_API_URL)
  /** id → iframe element. Populated by the iframe ref callback. */
  const iframeRefs = useRef<Map<string, HTMLIFrameElement>>(new Map())
  /** Monotonic counter for preview ids; ensures stable React keys + ordering. */
  const counterRef = useRef(0)

  const messageHandler = useCallback((e: MessageEvent) => {
    if (e.data?.type !== 'stimulize-history') return
    // Match the iframe by source so each preview's history goes back to
    // its own pane. Without this, history-from-preview-2 could overwrite
    // preview-1's pane.
    for (const [id, iframe] of iframeRefs.current.entries()) {
      if (iframe.contentWindow === e.source) {
        setPreviews((prev) =>
          prev.map((p) => (p.id === id ? { ...p, history: e.data.payload } : p))
        )
        return
      }
    }
  }, [])

  useEffect(() => {
    window.addEventListener('message', messageHandler)
    return () => window.removeEventListener('message', messageHandler)
  }, [messageHandler])

  // Revoke any leftover blob URLs on unmount so navigating away doesn't leak.
  useEffect(() => {
    return () => {
      for (const p of previews) {
        URL.revokeObjectURL(p.blobUrl)
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const buildHtml = (): string => {
    // The widget honors `apiBaseUrl` only when `beta: true` per
    // docs/api-reference.md "Widget JavaScript API". In dev we always pass
    // both so the preview hits whichever backend the user typed in
    // (defaults to localhost:5001). In production builds we omit both so
    // the widget uses its hardcoded prod URL.
    const initOptions = isDev
      ? {
          element: '#chatroom-container',
          chatroomId,
          beta: true,
          apiBaseUrl: hostnameOverride || CHATROOM_API_URL,
        }
      : {
          element: '#chatroom-container',
          chatroomId,
        }

    // Where to load chatroom.min.js from. In dev, use the override hostname
    // so we hit the local backend's bundled widget. In prod, use the CDN.
    const widgetScriptUrl = isDev
      ? CHATROOM_WIDGET_URL
      : 'https://cdn.stimulize.org/chatroom.min.js'

    return `<!DOCTYPE html>
<html><head>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
  #chatroom-container { height: 100vh; }
</style>
</head><body>
<div id="chatroom-container"></div>
<script src="https://cdn.jsdelivr.net/npm/jquery@3.7.1/dist/jquery.min.js"><\/script>
<script>
  var script = document.createElement("script");
  script.src = ${JSON.stringify(widgetScriptUrl)};
  script.onload = function() {
    StimulizeChatroom.init(${JSON.stringify(initOptions)});
  };
  document.head.appendChild(script);

  window.addEventListener("message", function(e) {
    if (e.data && e.data.type === "stimulize-get-history") {
      var history = StimulizeChatroom.getHistory();
      var payload;
      if (e.data.format === "json") {
        payload = JSON.stringify(history, null, 2);
      } else {
        payload = history.map(function(m) {
          var tag = m.role === "ai" ? " [AI]" : m.role === "system" ? " [SYS]" : "";
          return "[" + m.sender + tag + "] " + m.content;
        }).join("\\n");
      }
      parent.postMessage({ type: "stimulize-history", payload: payload }, "*");
    }
  });
<\/script>
</body></html>`
  }

  const launch = async (skipSave = false) => {
    if (!skipSave && onSaveBeforeLaunch) {
      setLaunching(true)
      try {
        await onSaveBeforeLaunch()
      } catch {
        // The save handler surfaces its own error message; just abort the launch.
        setLaunching(false)
        return
      }
      setLaunching(false)
    }

    const html = buildHtml()
    const blob = new Blob([html], { type: 'text/html' })
    const blobUrl = URL.createObjectURL(blob)
    counterRef.current += 1
    const id = `preview-${counterRef.current}`
    setPreviews((prev) => [...prev, { id, blobUrl, history: '' }])
  }

  const stop = (id: string) => {
    setPreviews((prev) => {
      const target = prev.find((p) => p.id === id)
      if (target) URL.revokeObjectURL(target.blobUrl)
      return prev.filter((p) => p.id !== id)
    })
    iframeRefs.current.delete(id)
  }

  const requestHistory = (id: string, format: 'json' | 'text') => {
    const iframe = iframeRefs.current.get(id)
    if (!iframe?.contentWindow) return
    iframe.contentWindow.postMessage({ type: 'stimulize-get-history', format }, '*')
  }

  const launchLabel = onSaveBeforeLaunch ? 'Save & Launch Preview' : 'Launch Preview'

  return (
    <div>
      <h3 style={{ marginBottom: 12 }}>Widget Preview</h3>

      {isDev && (
        <div style={{ marginBottom: 12 }}>
          <div style={{ marginBottom: 4, fontSize: 13, fontWeight: 500 }}>
            API Base URL <span style={{ color: '#86909c', fontWeight: 'normal' }}>(dev only)</span>
          </div>
          <Input
            value={hostnameOverride}
            onChange={setHostnameOverride}
            disabled={previews.length > 0}
            placeholder={CHATROOM_API_URL}
            style={{ maxWidth: 500 }}
          />
        </div>
      )}

      <Space style={{ marginBottom: 12 }}>
        <Button type="primary" loading={launching} onClick={() => launch()}>
          {launchLabel}
        </Button>
      </Space>

      {previews.length === 0 ? (
        <div style={{
          border: '1px solid #e5e6eb', borderRadius: 8, height: 500,
          background: '#fafafa', display: 'flex', alignItems: 'center',
          justifyContent: 'center', color: '#86909c', fontSize: 14,
        }}>
          {onSaveBeforeLaunch
            ? 'Click "Save & Launch Preview" to save the chatroom and test the widget'
            : 'Click "Launch Preview" to test the widget'}
        </div>
      ) : (
        // flex with equal-width columns so each new preview shrinks the
        // existing ones proportionally. A min-width keeps each pane usable;
        // beyond that the row wraps onto the next line. The "Launch
        // another preview" tile lives at the end of the row so it stays
        // adjacent to the existing previews even after wrapping.
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'stretch' }}>
          {previews.map((p, i) => (
            <div
              key={p.id}
              style={{
                flex: '1 1 360px',
                minWidth: 320,
                display: 'flex',
                flexDirection: 'column',
              }}
            >
              <div style={{
                display: 'flex', justifyContent: 'space-between',
                alignItems: 'center', marginBottom: 8,
              }}>
                <span style={{ fontSize: 13, fontWeight: 500 }}>Preview #{i + 1}</span>
                <Button size="mini" status="danger" onClick={() => stop(p.id)}>Stop</Button>
              </div>
              <div style={{
                border: '1px solid #e5e6eb', borderRadius: 8,
                overflow: 'hidden', height: 500, background: '#fafafa',
              }}>
                <iframe
                  ref={(el) => {
                    if (el) iframeRefs.current.set(p.id, el)
                    else iframeRefs.current.delete(p.id)
                  }}
                  src={p.blobUrl}
                  style={{ width: '100%', height: '100%', border: 'none' }}
                />
              </div>
              <Space style={{ marginTop: 8 }}>
                <Button size="mini" onClick={() => requestHistory(p.id, 'json')}>History JSON</Button>
                <Button size="mini" onClick={() => requestHistory(p.id, 'text')}>History Text</Button>
              </Space>
              {p.history && (
                <pre style={{
                  marginTop: 6, padding: 10, background: '#1e1e1e', color: '#d4d4d4',
                  borderRadius: 4, fontFamily: 'monospace', fontSize: 11,
                  whiteSpace: 'pre-wrap', maxHeight: 240, overflowY: 'auto',
                }}>{p.history}</pre>
              )}
            </div>
          ))}
          {/* "Launch another preview" tile — narrower than a real preview
              so it doesn't take the same horizontal share but still looks
              like a column in the row. Skips save (the row was already
              created from a saved state). */}
          <div
            style={{
              flex: '0 0 180px',
              minWidth: 160,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'stretch',
              justifyContent: 'flex-start',
            }}
          >
            {/* Spacer matches the per-preview header row height so the
                button aligns with the iframe top. */}
            <div style={{ height: 32, marginBottom: 8 }} />
            <Button
              long
              type="outline"
              onClick={() => launch(true)}
              style={{
                height: 500,
                borderStyle: 'dashed',
                fontSize: 13,
                whiteSpace: 'normal',
                lineHeight: 1.4,
              }}
            >
              + Launch another preview
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
