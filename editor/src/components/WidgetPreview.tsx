import { useState, useRef, useEffect, useCallback } from 'react'
import { Button, Space } from '@arco-design/web-react'
import { CHATROOM_API_URL } from '../config'

interface Props {
  chatroomId: string
}

export default function WidgetPreview({ chatroomId }: Props) {
  const [running, setRunning] = useState(false)
  const [historyOutput, setHistoryOutput] = useState('')
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const blobUrlRef = useRef('')

  const messageHandler = useCallback((e: MessageEvent) => {
    if (e.data?.type === 'stimulize-history') {
      setHistoryOutput(e.data.payload)
    }
  }, [])

  useEffect(() => {
    window.addEventListener('message', messageHandler)
    return () => window.removeEventListener('message', messageHandler)
  }, [messageHandler])

  const launch = () => {
    const html = `<!DOCTYPE html>
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
  script.src = ${JSON.stringify(CHATROOM_API_URL + '/chatroom.min.js')};
  script.onload = function() {
    StimulizeChatroom.init({
      element: "#chatroom-container",
      chatroomId: ${JSON.stringify(chatroomId)},
      apiBaseUrl: ${JSON.stringify(CHATROOM_API_URL)}
    });
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

    const blob = new Blob([html], { type: 'text/html' })
    blobUrlRef.current = URL.createObjectURL(blob)
    setRunning(true)
  }

  const stop = () => {
    setRunning(false)
    setHistoryOutput('')
    if (blobUrlRef.current) {
      URL.revokeObjectURL(blobUrlRef.current)
      blobUrlRef.current = ''
    }
  }

  const requestHistory = (format: 'json' | 'text') => {
    const iframe = iframeRef.current
    if (!iframe?.contentWindow) return
    iframe.contentWindow.postMessage({ type: 'stimulize-get-history', format }, '*')
  }

  return (
    <div>
      <h3 style={{ marginBottom: 12 }}>Widget Preview</h3>
      <Space style={{ marginBottom: 12 }}>
        {!running ? (
          <Button type="primary" onClick={launch}>Launch Preview</Button>
        ) : (
          <Button status="danger" onClick={stop}>Stop</Button>
        )}
      </Space>

      <div style={{ border: '1px solid #e5e6eb', borderRadius: 8, overflow: 'hidden', height: 500, background: '#fafafa' }}>
        {running ? (
          <iframe
            ref={iframeRef}
            src={blobUrlRef.current}
            style={{ width: '100%', height: '100%', border: 'none' }}
          />
        ) : (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: '#86909c', fontSize: 14 }}>
            Click "Launch Preview" to test the widget
          </div>
        )}
      </div>

      {running && (
        <Space style={{ marginTop: 12 }}>
          <Button onClick={() => requestHistory('json')}>History JSON</Button>
          <Button onClick={() => requestHistory('text')}>History Text</Button>
        </Space>
      )}

      {historyOutput && (
        <pre style={{
          marginTop: 8, padding: 12, background: '#1e1e1e', color: '#d4d4d4',
          borderRadius: 4, fontFamily: 'monospace', fontSize: 12,
          whiteSpace: 'pre-wrap', maxHeight: 300, overflowY: 'auto',
        }}>{historyOutput}</pre>
      )}
    </div>
  )
}
