import { readFileSync } from 'fs';
import { join } from 'path';

test('normal production synthesis retains released chatroom features', () => {
  const { context } = JSON.parse(readFileSync(join(__dirname, '..', 'cdk.json'), 'utf8'));
  expect(context.enableAiBatch).toBe('true');
  expect(context.enablePromptAttachments).toBe('true');
  expect(context.chatroomServiceMode).toBe('normal');
});
