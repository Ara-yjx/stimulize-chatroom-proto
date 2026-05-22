import { useState } from 'react'
import { Input, Button, Message, Switch } from '@arco-design/web-react'
import { CHATROOM_API_URL, CHATROOM_WIDGET_URL } from '../config'

const TextArea = Input.TextArea

interface Props {
  chatroomId: string
}

/**
 * Generates the embed script that researchers paste into Qualtrics. Has a
 * "beta" toggle that produces a snippet with `beta: true` and `apiBaseUrl`
 * set to a custom hostname. The widget honors `apiBaseUrl` only when
 * `beta: true` per docs/api-reference.md "Widget JavaScript API".
 */
export default function ScriptGenerator({ chatroomId }: Props) {
  const [beta, setBeta] = useState(false)
  const [apiBaseUrl, setApiBaseUrl] = useState(CHATROOM_API_URL)
  const [snippet, setSnippet] = useState('')

  const generate = () => {
    const initBlock = beta
      ? `    StimulizeChatroom.init({
      element: chatDiv,
      chatroomId: "${chatroomId}",
      beta: true,
      apiBaseUrl: "${apiBaseUrl}"
    });`
      : `    StimulizeChatroom.init({
      element: chatDiv,
      chatroomId: "${chatroomId}"
    });`

    const widgetScriptUrl = CHATROOM_WIDGET_URL

    const script = `Qualtrics.SurveyEngine.addOnload(function() {
  var chatDiv = document.createElement("div");
  chatDiv.style.height = "500px";
  this.questionContainer.appendChild(chatDiv);
  var s = document.createElement("script");
  s.src = "${widgetScriptUrl}";
  s.onload = function() {
${initBlock}
  };
  document.head.appendChild(s);
});`
    setSnippet(script)
  }

  const copySnippet = async () => {
    try {
      await navigator.clipboard.writeText(snippet)
      Message.success('Copied to clipboard')
    } catch {
      const el = document.createElement('textarea')
      el.value = snippet
      document.body.appendChild(el)
      el.select()
      document.execCommand('copy')
      document.body.removeChild(el)
      Message.success('Copied to clipboard')
    }
  }

  return (
    <div>
      <h3 style={{ marginBottom: 12 }}>Generate Embed Script</h3>

      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
        <Switch checked={beta} onChange={setBeta} />
        <span style={{ fontSize: 13 }}>Beta mode (custom backend URL)</span>
      </div>

      {beta && (
        <div style={{ marginBottom: 12 }}>
          <div style={{ marginBottom: 4, fontSize: 13, fontWeight: 500 }}>API Base URL</div>
          <Input value={apiBaseUrl} onChange={setApiBaseUrl} style={{ maxWidth: 500 }} />
        </div>
      )}

      <Button type="primary" onClick={generate} style={{ marginBottom: 12 }}>
        Generate Script
      </Button>

      {snippet && (
        <div>
          <TextArea
            readOnly
            value={snippet}
            autoSize={{ minRows: 12, maxRows: 24 }}
            style={{ fontFamily: 'monospace', fontSize: 12 }}
          />
          <Button onClick={copySnippet} style={{ marginTop: 8 }}>Copy to Clipboard</Button>
        </div>
      )}
    </div>
  )
}
