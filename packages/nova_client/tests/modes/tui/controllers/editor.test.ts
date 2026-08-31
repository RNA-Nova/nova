/**
 * EditorController 测试：扩展编辑器热替换（registerEditor → maybeSwapEditor）
 * + 提交管线路由（submitText 的 !!/!/steer/followUp 分发）+ 队列还原。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { NovaUIRuntime, SlotRegistry, commandSlot, createExtensionUIAPI, editorSlot } from 'nova-client';

import { EditorController, type EditorRef } from '../../../../src/modes/tui/controllers/editor.js';

/** 最小 EditorComponent 实现（测试替身）。 */
function makeFakeEditor(initial = '') {
  return {
    text: initial,
    wired: false,
    getText() {
      return this.text;
    },
    setText(text: string) {
      this.text = text;
    },
    handleInput: () => {},
    render: () => ['fake-editor'],
    invalidate: () => {},
  };
}

function makeController(runtime: NovaUIRuntime) {
  const editorRef: EditorRef = { current: makeFakeEditor() as never };
  const calls = { info: [] as string[], error: [] as string[] };
  const transcript = {
    addInfo: (m: string) => void calls.info.push(m),
    addError: (e: unknown) => void calls.error.push(String(e)),
  };
  const container = {
    children: [] as unknown[],
    clear() {
      this.children = [];
    },
    addChild(c: unknown) {
      this.children.push(c);
    },
  };
  const tui = { setFocus: () => {}, requestRender: () => {} };
  const controller = new EditorController(
    editorRef,
    runtime,
    '/tmp',
    transcript as never,
    { isActive: false } as never,
    {} as never, // theme
    {} as never, // settings
    tui as never,
    container as never,
  );
  return { controller, editorRef, calls, container };
}

describe('registerEditor 扩展编辑器热替换', () => {
  it('注册工厂 → maybeSwapEditor 热替换（文本迁移 + 重接线 + 槽位换人）', () => {
    const runtime = new NovaUIRuntime();
    const { controller, editorRef, calls, container } = makeController(runtime);
    (editorRef.current as { text: string }).text = '未完成的输入';

    const api = createExtensionUIAPI({ slots: runtime.slots, source: 'vim-pkg' });
    const fake = makeFakeEditor();
    api.registerEditor(() => fake);
    assert.equal(runtime.slots.sourceOf(editorSlot()), 'vim-pkg');

    controller.maybeSwapEditor();
    assert.equal(editorRef.current, fake as never); // 换人
    assert.equal(fake.text, '未完成的输入'); // 文本迁移
    assert.equal(fake.onSubmit !== undefined, true); // 重接线（wire 挂回调）
    assert.equal(container.children[0], fake); // 槽位换人
    assert.deepEqual(calls.info, ['扩展编辑器已启用']);
  });

  it('幂等：同一工厂不重复替换', () => {
    const runtime = new NovaUIRuntime();
    const { controller, editorRef } = makeController(runtime);
    const factory = () => makeFakeEditor();
    runtime.slots.register(editorSlot(), factory, 'pkg');
    controller.maybeSwapEditor();
    const first = editorRef.current;
    controller.maybeSwapEditor(); // 同工厂——不换
    assert.equal(editorRef.current, first);
  });

  it('契约防御：工厂产物缺必需方法 → 错误提示，不换人', () => {
    const runtime = new NovaUIRuntime();
    const { controller, editorRef, calls } = makeController(runtime);
    const original = editorRef.current;
    runtime.slots.register(editorSlot(), () => ({ notAnEditor: true }), 'bad-pkg');
    controller.maybeSwapEditor();
    assert.equal(editorRef.current, original); // 未换
    assert.equal(calls.error.length, 1);
    assert.match(calls.error[0]!, /EditorComponent 契约/);
  });

  it('无注册工厂时不动作', () => {
    const runtime = new NovaUIRuntime();
    const { controller, editorRef } = makeController(runtime);
    const original = editorRef.current;
    controller.maybeSwapEditor();
    assert.equal(editorRef.current, original);
  });
});

// ---------------------------------------------------------------------------
// 提交管线路由（submitText）与队列还原（dequeueToEditor）
// ---------------------------------------------------------------------------

/** 行为桩 runtime（记录 prompt/invokeUserTool/clearQueue 调用）。 */
function makeStubRuntime(status: 'idle' | 'working' = 'idle') {
  const calls = {
    prompt: [] as Array<{ text: string; options?: unknown }>,
    userTools: [] as Array<{ name: string; params: Record<string, unknown> }>,
    queue: { steering: [] as string[], followUp: [] as string[] },
  };
  const runtime = {
    store: { status },
    slots: new SlotRegistry(), // submitText 的 slash 分支查扩展命令用
    prompt: async (text: string, options?: unknown) => {
      calls.prompt.push({ text, options });
    },
    invokeUserTool: async (name: string, params: Record<string, unknown>) => {
      calls.userTools.push({ name, params });
    },
    clearQueue: async () => {
      const result = {
        steering: [...calls.queue.steering],
        followUp: [...calls.queue.followUp],
      };
      calls.queue.steering = [];
      calls.queue.followUp = [];
      return result;
    },
  } as unknown as NovaUIRuntime;
  return { runtime, calls };
}

function makePipelineController(status: 'idle' | 'working' = 'idle') {
  const { runtime, calls } = makeStubRuntime(status);
  const editorRef: EditorRef = { current: makeFakeEditor() as never };
  const transcript = { addInfo: () => {}, addError: () => {} };
  const controller = new EditorController(
    editorRef,
    runtime,
    '/tmp',
    transcript as never,
    { isActive: false } as never,
    {} as never, // theme
    {} as never, // settings
    { setFocus: () => {}, requestRender: () => {} } as never,
    { clear: () => {}, addChild: () => {} } as never,
  );
  return { controller, editorRef, calls, runtime };
}

describe('submitText 提交路由', () => {
  it('!! 前缀：bash 不进上下文（exclude_from_context）', () => {
    const { controller, calls } = makePipelineController();
    controller.submitText('!!ls -la');
    assert.deepEqual(calls.userTools, [
      { name: 'bash', params: { command: 'ls -la', exclude_from_context: true } },
    ]);
    assert.deepEqual(calls.prompt, []);
  });

  it('! 前缀：bash 进上下文', () => {
    const { controller, calls } = makePipelineController();
    controller.submitText('!pwd');
    assert.deepEqual(calls.userTools, [{ name: 'bash', params: { command: 'pwd' } }]);
  });

  it('idle 普通文本：prompt 无 streamingBehavior', () => {
    const { controller, calls } = makePipelineController('idle');
    controller.submitText('你好');
    assert.deepEqual(calls.prompt, [{ text: '你好', options: undefined }]);
  });

  it('working 普通文本：缺省 steer；followUp 选项排队', () => {
    const { controller, calls } = makePipelineController('working');
    controller.submitText('打断一下');
    controller.submitText('等会再说', { followUp: true });
    assert.deepEqual(calls.prompt, [
      { text: '打断一下', options: { streamingBehavior: 'steer' } },
      { text: '等会再说', options: { streamingBehavior: 'followUp' } },
    ]);
  });

  it('slash 命令走 runSlashCommand（不进 prompt 通道）', () => {
    const { controller, calls } = makePipelineController();
    // runSlashCommand 内部走 promptCancellable——桩没有该方法，替换成探针
    let slash = '';
    (controller as unknown as { runSlashCommand: (c: string) => void }).runSlashCommand = (
      c,
    ) => (slash = c);
    controller.submitText('/compact');
    assert.equal(slash, '/compact');
    assert.deepEqual(calls.prompt, []);
  });

  it('/model、/scoped-models 无扩展注册时落后端命令通道（包自持 UI 在 bundle）', () => {
    const { controller, calls } = makePipelineController();
    const slashes: string[] = [];
    (controller as unknown as { runSlashCommand: (c: string) => void }).runSlashCommand = (
      c,
    ) => void slashes.push(c);
    controller.submitText('/model');
    controller.submitText('/scoped-models');
    assert.deepEqual(slashes, ['/model', '/scoped-models']);
    assert.deepEqual(calls.prompt, []);
  });

  it('runCommand：扩展命令 slot 优先，缺席落后端 slash（双 Esc/ctrl+l 共用入口）', () => {
    const { controller, runtime } = makePipelineController();
    const executed: string[] = [];
    runtime.slots.register(
      commandSlot('model'),
      (args: string) => void executed.push(args),
      'test-pkg',
    );
    const slashes: string[] = [];
    (controller as unknown as { runSlashCommand: (c: string) => void }).runSlashCommand = (
      c,
    ) => void slashes.push(c);
    controller.runCommand('model'); // 扩展命中——本地执行
    assert.deepEqual(executed, ['']);
    assert.deepEqual(slashes, []);
    controller.runCommand('fork'); // 未注册——落后端
    assert.deepEqual(slashes, ['/fork']);
  });

  it('runCommand：agent.yaml 允许集/排除集对程序化入口同样生效（热键不绕过白名单）', () => {
    const { controller, runtime } = makePipelineController();
    const executed: string[] = [];
    runtime.slots.register(
      commandSlot('model'),
      (args: string) => void executed.push(args),
      'test-pkg',
    );
    const slashes: string[] = [];
    const infos: string[] = [];
    (controller as unknown as { runSlashCommand: (c: string) => void }).runSlashCommand = (
      c,
    ) => void slashes.push(c);
    // addInfo 默认空操作，换探针
    (controller as unknown as { transcript: { addInfo: (m: string) => void } }).transcript =
      { addInfo: (m: string) => void infos.push(m) };
    // 快照：model 不在允许集；fork 在允许集
    (runtime.store as { currentSnapshot?: unknown }).currentSnapshot = {
      allowedCommands: ['fork'],
      disabledCommands: [],
    };
    controller.runCommand('model'); // 被允许集排除——拦
    assert.deepEqual(executed, []);
    assert.deepEqual(slashes, []);
    assert.equal(infos.length, 1);
    assert.ok(infos[0].includes('/model'));
    controller.runCommand('fork'); // 允许集内——放行（未注册 slot 落后端）
    assert.deepEqual(slashes, ['/fork']);
    // disabledCommands 黑名单同样拦
    (runtime.store as { currentSnapshot?: unknown }).currentSnapshot = {
      allowedCommands: null,
      disabledCommands: ['model'],
    };
    controller.runCommand('model');
    assert.deepEqual(executed, []);
    assert.equal(infos.length, 2);
  });

  it('setupAutocomplete：RPC 返回的 null 名命令被丢弃（pi-tui null value 崩溃回归）', async () => {
    const { controller, runtime, editorRef } = makePipelineController();
    (runtime as unknown as { invoke: (m: string) => Promise<unknown> }).invoke = async (
      m: string,
    ) =>
      m === 'getCommands'
        ? {
            commands: [
              { name: 'compact', description: '压缩' },
              { name: null, description: '坏条目' }, // 线上 null（invocation_name None 回归）
              { name: 'tree', description: '导航' },
            ],
          }
        : {};
    let captured: { commands?: Array<{ name: string }> } | undefined;
    (
      editorRef.current as unknown as {
        setAutocompleteProvider: (p: unknown) => void;
      }
    ).setAutocompleteProvider = (p: unknown) => {
      captured = p as { commands?: Array<{ name: string }> };
    };
    await controller.setupAutocomplete();
    const names = (captured?.commands ?? []).map((c) => c.name);
    assert.ok(names.length > 0);
    assert.ok(names.every((n) => typeof n === 'string' && n.length > 0));
    assert.ok(names.includes('compact') && names.includes('tree'));
  });

  it('setupAutocomplete：三源按分发现实去重（slot 描述覆盖 RPC 同名，本地命令在内）', async () => {
    const { controller, runtime, editorRef } = makePipelineController();
    (runtime as unknown as { invoke: (m: string) => Promise<unknown> }).invoke = async (
      m: string,
    ) =>
      m === 'getCommands'
        ? {
            commands: [
              { name: 'tree', description: '后端版描述' },
              { name: 'compact', description: '压缩' },
            ],
          }
        : {};
    // slot 注册同名 tree（带真实描述）与独有命令 deploy（无描述）
    const treeFn = (args: string) => void args;
    (treeFn as { description?: string }).description = '导航会话树（包自持）';
    runtime.slots.register(commandSlot('tree'), treeFn, 'test-pkg');
    runtime.slots.register(commandSlot('deploy'), (args: string) => void args, 'test-pkg');
    let captured: { commands?: Array<{ name: string; description?: string }> } | undefined;
    (
      editorRef.current as unknown as {
        setAutocompleteProvider: (p: unknown) => void;
      }
    ).setAutocompleteProvider = (p: unknown) => {
      captured = p as { commands?: Array<{ name: string; description?: string }> };
    };
    await controller.setupAutocomplete();
    const byName = new Map((captured?.commands ?? []).map((c) => [c.name, c]));
    // tree 只出现一次，且为 slot 描述（分发现实：slot 优先）
    assert.equal(byName.get('tree')?.description, '导航会话树（包自持）');
    assert.equal(byName.get('compact')?.description, '压缩');
    assert.equal(byName.get('deploy')?.description, '扩展命令');
    assert.equal(byName.get('theme')?.description, '切换主题（移动即预览，Enter 持久化）');
    // 无重复名
    const names = [...byName.keys()];
    assert.equal(new Set(names).size, names.length);
  });

  it('Node 扩展命令优先于后端命令（registerCommand 命中即本地执行）', () => {
    const { controller, calls, runtime } = makePipelineController();
    const executed: string[] = [];
    runtime.slots.register(
      commandSlot('deploy'),
      (args: string) => void executed.push(args),
      'test-pkg',
    );
    let backendSlash = '';
    (controller as unknown as { runSlashCommand: (c: string) => void }).runSlashCommand = (
      c,
    ) => (backendSlash = c);
    controller.submitText('/deploy prod now');
    assert.deepEqual(executed, ['prod now']); // 参数透传
    assert.equal(backendSlash, ''); // 未走后端
    assert.deepEqual(calls.prompt, []);
  });
});

describe('dequeueToEditor 队列还原', () => {
  it('队列内容按时间序填回编辑器（现有草稿附后）', async () => {
    const { controller, editorRef, calls } = makePipelineController();
    calls.queue.steering = ['第一条 steer'];
    calls.queue.followUp = ['随后 follow'];
    (editorRef.current as { text: string }).text = '草稿';
    await controller.dequeueToEditor();
    assert.equal(
      (editorRef.current as { text: string }).text,
      '第一条 steer\n随后 follow\n草稿',
    );
    assert.deepEqual(calls.queue.steering, []); // 队列已清
    assert.deepEqual(calls.queue.followUp, []);
  });

  it('空队列不动编辑器', async () => {
    const { controller, editorRef } = makePipelineController();
    (editorRef.current as { text: string }).text = '保持';
    await controller.dequeueToEditor();
    assert.equal((editorRef.current as { text: string }).text, '保持');
  });
});
