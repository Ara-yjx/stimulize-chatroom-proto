import { useState } from 'react'
import { Input, Button, Message, Typography } from '@arco-design/web-react'
import { CHATROOM_WIDGET_URL } from '../config'
import { IconCopy } from '@arco-design/web-react/icon'

const TextArea = Input.TextArea
const Text = Typography.Text

interface Props {
  chatroomId: string
}

/**
 * Generates the embed script that researchers paste into Qualtrics.
 * During beta we always force the runtime API hostname explicitly so the
 * widget does not fall back to `chatroom.stimulize.org` before DNS is live.
 */
export default function ScriptGenerator({ chatroomId }: Props) {
  // const [apiBaseUrl, setApiBaseUrl] = useState(CHATROOM_API_URL)
  const [snippet, setSnippet] = useState('')

  const generate = () => {
    const initBlock = `    StimulizeChatroom.init({
      parentElement: qualtricsQuestion.questionContainer,
      elementStyle: { height: "500px" },
      qualtricsQuestion: qualtricsQuestion,
      chatroomId: "${chatroomId}",
    });`

    const widgetScriptUrl = CHATROOM_WIDGET_URL

    const script = `Qualtrics.SurveyEngine.addOnload(function() {
  var qualtricsQuestion = this;
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

      {/* <div style={{ marginBottom: 12 }}>
        <div style={{ marginBottom: 4, fontSize: 13, fontWeight: 500 }}>Runtime API Base URL</div>
        <Input value={apiBaseUrl} onChange={setApiBaseUrl} style={{ maxWidth: 500 }} />
      </div> */}

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
          <Button onClick={copySnippet} style={{ marginTop: 8 }}><IconCopy /> Copy to Clipboard</Button>
          <br />
          <Text>
            You also need to create two Embeded Data fields in Qualtrics: <br />
            <code>QUALTRICS_CHATROOM_HISTORY</code>
            <span> and </span>
            <code>QUALTRICS_CHATROOM_HISTORY_JSON</code>.
          </Text>
        </div>
      )}
    </div>
  )
}
