/** ExtensionUIAPI 注册面测试：命令描述/参数补全的函数对象附着。 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { createExtensionUIAPI, SlotRegistry } from 'nova-client';

describe('createExtensionUIAPI · registerCommand', () => {
  it('description 与 getArgumentCompletions 附着在注册函数上（补全目录消费）', () => {
    const slots = new SlotRegistry();
    const api = createExtensionUIAPI({ slots, source: 'test-pkg' });
    api.registerCommand('demo', {
      description: '演示命令',
      getArgumentCompletions: (prefix) =>
        prefix.startsWith('a') ? [{ value: 'alpha', label: 'alpha' }] : null,
      handler: () => {},
    });
    const fn = slots.resolve('command:demo' as never) as unknown as {
      description?: string;
      getArgumentCompletions?: (p: string) => unknown;
    };
    assert.equal(fn.description, '演示命令');
    assert.deepEqual(fn.getArgumentCompletions?.('a'), [{ value: 'alpha', label: 'alpha' }]);
    assert.equal(fn.getArgumentCompletions?.('b'), null);
  });

  it('无补全时不附着（editor 回退 null）', () => {
    const slots = new SlotRegistry();
    const api = createExtensionUIAPI({ slots, source: 'test-pkg' });
    api.registerCommand('plain', { handler: () => {} });
    const fn = slots.resolve('command:plain' as never) as {
      getArgumentCompletions?: unknown;
    };
    assert.equal(fn.getArgumentCompletions, undefined);
  });
});
