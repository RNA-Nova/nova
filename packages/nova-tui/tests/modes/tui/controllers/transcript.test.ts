/**
 * custom-entry 渲染器消费点测试（transcript：entry:<customType> slot 解析 →
 * 块适配；无注册走 CustomMessageView 兜底）。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  NovaUIRuntime,
  createExtensionUIAPI,
  type TranscriptEntry,
} from 'nova-tui';
import { Container } from '@earendil-works/pi-tui';

import { TranscriptController } from '../../../../src/modes/tui/controllers/transcript.js';
import { registerBuiltinBlocks } from '../../../../src/modes/tui/blocks/index.js';
import type { ExpansionState } from '../../../../src/modes/tui/components/transcript/expansion.js';

function makeTranscript(entries: TranscriptEntry[]) {
  const runtime = new NovaUIRuntime({ slotsBootstrap: registerBuiltinBlocks });
  // store 桩：entries 直给（TranscriptController.onChange 只读它）
  (runtime as unknown as { store: unknown }).store = {
    entries,
    subscribe: () => {},
  };
  const chatContainer = new Container();
  const expansion: ExpansionState = { expanded: false };
  const controller = new TranscriptController(
    { requestRender: () => {} },
    chatContainer,
    runtime,
    expansion,
  );
  return { runtime, controller, chatContainer };
}

const customEntry: TranscriptEntry = {
  kind: 'custom',
  id: 'e1',
  customType: 'deploy-log',
  data: { content: '部署完成' },
} as TranscriptEntry;

describe('transcript · custom-entry 渲染器', () => {
  it('注册的 entry:<customType> 渲染器产块渲染', () => {
    const { runtime, controller, chatContainer } = makeTranscript([customEntry]);
    createExtensionUIAPI({ slots: runtime.slots, source: 'pkg' }).registerEntryRenderer(
      'deploy-log',
      (entry) => [
        { kind: 'markdown', text: `**自定义渲染**: ${(entry.data as { content: string }).content}` },
      ],
    );
    controller.onChange();
    const out = chatContainer.render(100).join('\n');
    assert.match(out, /自定义渲染/);
    assert.match(out, /部署完成/);
    assert.doesNotMatch(out, /\[deploy-log\]/); // 不走兜底的标签形态
  });

  it('无注册渲染器 → CustomMessageView 兜底（[customType] 标签）', () => {
    const { controller, chatContainer } = makeTranscript([customEntry]);
    controller.onChange();
    const out = chatContainer.render(100).join('\n');
    assert.match(out, /\[deploy-log\]/);
  });
});
