## Raw Requirements
AI Chatroom实际使用中的几个点：
1. 每一个ai需要可以命名（比如在实验中，我需要命名一个ai叫做condition1另一个ai叫做condition2）这个命名后面可以跟随机数字如果需要这些数字在后台作为unique id。但是这个命名需要在后台记录数据中体现，方便数据分析。（P2）
2. 每一个ai在chatroom里面的名字可以允许customize，比如我可以把名字改为John, Tom, 或Alice这种可以体现某种身份的名字。（P2）
4. AI在对话过程中好像没有很主动的和真人去聊，有一点自说自话。最好可以对人们的说话有所反馈。(P4)
5. ai是否拟人应该是一个可以打开的选项，然后这个选项开启（需要拟人）的时候具体里面最好能够调节一些参数（比如ai的各种拟人方式的程度，如回复速度、断句、错字等）。(P2)
6. ai的每句话都是小写字母有一些假，这个可以改为有些是有些不是。(P4)

9. 断线重连可能是需要的，不然如果是固定4人的discussion，一个人断线了可能会造成整个discussion数据无法使用。(P3)
10. 每一种model的价格可以标注出来(P4)
11. mode，fixed AIs, Total Participants, Target Human Count这四个选择有些confusinng。可以直接换成俩，就是一个输入人类被试人数，一个数据ai agent人数。用户可以直接知道怎么用。(P4)

## Other requirement that we'll park for now (ignore this, for human note only)
3. AI在chat开始以后有一会儿才出现，最好chat开始的时候ai就可以先出来聊。(P4)

8. 在link放入Qualtrics时，那个block最好能够显示某个符号，或者比如这个chat聊天框在block里显现，这样对于researchers来说更方便可以看见。(P2)



## My review of each requirement

1. 每一个ai需要可以命名（比如在实验中，我需要命名一个ai叫做condition1另一个ai叫做condition2）这个命名后面可以跟随机数字如果需要这些数字在后台作为unique id。但是这个命名需要在后台记录数据中体现，方便数据分析

- Add an `internal_name` attribute for each AI persona/agent.
- `internal_name` is for analysis; it does not have to be the participant-visible chat name.
- It should be unique among the AIs in one chatroom setting. If needed, backend/editor can append a suffix for uniqueness.
- Store the resolved `internal_name` on the AI participant in the conversation DB.
- Include `internal_name` in structured ED JSON for AI messages.
- Include `internal_name` in formatted ED text for AI messages as:
  - `[John (condition1)] message text`
- Identity model:
  - `session_id`: runtime unique ID
  - `internal_name`: analysis label
  - `nickname`: participant-visible display name


2. 每一个ai在chatroom里面的名字可以允许customize，比如我可以把名字改为John, Tom, 或Alice这种可以体现某种身份的名字。（P2）

- Add a `nickname` attribute for each AI persona/agent.
- If `nickname` is set, do not randomly pick the AI display name when the conversation starts.
- If `nickname` is not set, keep current random nickname behavior.


4. AI在对话过程中好像没有很主动的和真人去聊，有一点自说自话。最好可以对人们的说话有所反馈。(P4)

- Improve the prompt to prioritize listening to and responding to other participants over rigidly sticking to the chatroom topic goal.
- This is a prompt/scaffold change, not a schema change.

5. ai是否拟人应该是一个可以打开的选项，然后这个选项开启（需要拟人）的时候具体里面最好能够调节一些参数（比如ai的各种拟人方式的程度，如回复速度、断句、错字等）。(P2)

- Add/use a `mimic_human` flag in chatroom setting.
- Start simple:
  - default `mimic_human=true`
  - if `mimic_human=true`, keep human-mimic scaffold/examples
  - if `mimic_human=false`, remove instructions/examples about mimicking a human and use a simple general instruction for participating in the group conversation as an AI assistant
- Rely on the provider model's default assistant behavior when `mimic_human=false`.
- Bedrock prompt cache likely needs two static prompt variants:
  - human-mimic static prefix
  - generic-AI-assistant static prefix
- Fine-tune controls such as reply speed, split messages, typo rate are future work.
- Create or update a prompt design doc before implementation.


6. ai的每句话都是小写字母有一些假，这个可以改为有些是有些不是。(P4)

- Improve our prompt and examples

7. Timer Min好像没什么用处还有些confusing，因为在qualtrics中人们可以通过隐藏下一步来保证参与者不离开。(P3)

- 没有timer min才会让被试confused：我不想聊了，为什么不能下一步？
- Make `timer_min_minutes` optional.
- `timer_min_minutes = null` means no minimum requirement.
- If `timer_min_minutes` is set and the widget is running in real Qualtrics:
  - hide the Qualtrics Next button until the minimum time is reached
  - refer to `amp-qualtrics/src/load.js` for how to hide Next
- If `timer_min_minutes` is null:
  - do not hide the Next button
  - show only the max-time guidance, e.g. "please stay at most {timerMax} minutes in the chatroom"
- In non-Qualtrics preview/local environments, do not attempt to hide Next.

9. 断线重连可能是需要的，不然如果是固定4人的discussion，一个人断线了可能会造成整个discussion数据无法使用。(P3)

- Store a reconnect record in localStorage:
  - `chatroom_id`
  - `conversation_id`
  - `session_id`
  - JWT token
  - token expiry
  - enough session info to restore nickname/avatar/chatroom setting
- Reconnect only for the same `chatroom_id`.
- Reconnect lobby sessions and active conversation sessions.
- Reconnect requires:
  - unexpired token
  - same chatroom
  - conversation not ended
- If token is expired, treat the old conversation as ended and join a new conversation.
- If token is unexpired but the server says the conversation is already ended, clear the reconnect record and join a new conversation.
- When a conversation ends normally, clear the reconnect record.
- A new Qualtrics respondent on the same browser should not reuse an old ended/expired conversation.

10. 每一种model的价格可以标注出来(P4)

- pure editor frontend change. Also mark which model supports caching, and tell that caching reduces xx% token usage.

11. mode，fixed AIs, Total Participants, Target Human Count这四个选择有些confusinng。可以直接换成俩，就是一个输入人类被试人数，一个数据ai agent人数。用户可以直接知道怎么用。(P4)

- Let's use these options
  - human count
  - ai count
  - `replace_human_with_ai_if_insufficient_human` boolean, configurable only when human count > 1
- Meaning:
  - `human_count`: desired human participant count
  - `ai_count`: desired AI participant count
  - if `replace_human_with_ai_if_insufficient_human=false`, wait timeout starts with available humans plus `ai_count` AIs
  - if `true`, wait timeout guarantees total participants equals `human_count + ai_count` by replacing missing humans with extra AIs
- No need for backward compatibility in the widget.
- Editor should transform old saved schema to the new schema for compatibility with existing beta chatrooms.
- Backend/runtime can continue storing normalized lower-level fields if useful, but editor-facing UI should expose the simplified model.


## Additional tech requirement

12. Add "temperature" for each AI

- Add chatroom-level `temperature` as the default.
- Add per-AI/persona `temperature` override.
- If per-AI temperature is undefined/null, use chatroom-level temperature.
- This mirrors model selection:
  - chatroom default model + per-AI model override
  - chatroom default temperature + per-AI temperature override
- Need to define validation range before implementation. Recommended default range: `0.0..1.0` unless Bedrock/model constraints require wider.

13. In the future (not now), we should totally split the "chatroom server" logic and "ai inference" logic. We don't design it right now, but keep this in mind when you design other features. 

14. Improve the code snippet that experiment designer should paste into Qualtrics - just pass widget an container element, and widget should create the chatroom element at the tail of container element by itself

- Add a new `parentElement` init option.
- Keep the existing `element` init option for compatibility.
- If `parentElement` is used:
  - widget creates its own child chatroom element
  - widget appends the child to `parentElement`
  - `elementStyle` can be applied to the generated child element
- Target generated snippet:

```javascript
Qualtrics.SurveyEngine.addOnload(function() {
  var s = document.createElement("script");
  s.src = "https://ara-yjx.github.io/stimulize-chatroom-proto/chatroom.min.js";
  s.onload = function() {
    StimulizeChatroom.init({
      parentElement: this.questionContainer,
      elementStyle: { height: "500px" },
      chatroomId: "scid_540a80a7-fcff-4943-bdb7-0e11fa9bb94b",
    });
  };
  document.head.appendChild(s);
});
```

15. hard-budget-cap feature

- see [token-usage-and-billing-design.md](./token-usage-and-billing-design.md)

16. Avoid duplicate joins in Qualtrics mobile preview

- In Qualtrics preview mode, desktop and mobile previews can render in separate iframes. If both initialize the widget, they can join the same chatroom as two separate participants and cause data collision.
- Use `window.frameElement?.id` to detect the Qualtrics preview iframe.
- If the frame id exists and equals `mobile-preview-view`:
  - do not initialize the widget
  - do not call `/auth/token`
  - show this message in the widget container:
    - `Please use the Qualtrics desktop preview to avoid data collision issue`
- If the frame id is `preview-view`, continue normal widget initialization.
- If no frame id is available, continue normal widget initialization.
- Keep the implementation decoupled:
  - isolate Qualtrics preview detection in a small environment/helper module
  - keep widget rendering and runtime join logic independent from this check
  - future full solution may still split state management from display rendering


