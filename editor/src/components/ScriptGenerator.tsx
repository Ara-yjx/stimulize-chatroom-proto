import { useState } from 'react'
import { Input, Button, Message } from '@arco-design/web-react'
import { CHATROOM_API_URL } from '../config'

const TextArea = Input.TextArea

interface Props {
  chatroomId: string
}

export default function ScriptGenerator({ chatroomId }: Props) {
  const [apiBaseUrl, setApiBaseUrl] = useState(CHATROOM_API_URL)
  const [snippet, setSnippet] = useState('')

  const generate = () => {
    const script = `Qualtrics.SurveyEngine.addOnload(function() {
  var chatDiv = document.createElement("div");
  chatDiv.style.height = "500px";
  this.questionContainer.appendChild(chatDiv);
  var s = document.createElement("script");
  s.src = "${apiBaseUrl}/chatroom.min.js";
  s.onload = function() {
    StimulizeChatroom.init({
      element: chatDiv,
      chatroomId: "${chatroomId}",
      apiBaseUrl: "${apiBaseUrl}"
    });
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
      <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end', marginBottom: 12 }}>
        <div style={{ flex: 1 }}>
          <div style={{ marginBottom: 4, fontSize: 13, fontWeight: 500 }}>API Base URL</div>
          <Input value={apiBaseUrl} onChange={setApiBaseUrl} />
        </div>
        <Button type="primary" onClick={generate}>Generate Script</Button>
      </div>

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
