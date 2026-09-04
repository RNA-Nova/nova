/**
 * nova-base 的扩展入口（frontend/tui/index.ts——全量扩展工厂）。
 *
 * 会话产品基础设施的 TUI 呈现半区：/tree 选择器、/todos 查看器、
 * /model 模型选择器、/scoped-models 池面板、/resume 会话选择器、
 * /fork 分叉选择器经 registerCommand + ctx.custom 模态挂载，不在宿主
 * 硬编码。后端同名命令保留作 headless 回退。
 */

import type { ExtensionUIAPI } from 'nova-tui';

import { questionDialogFactory } from './dialogs/question.js';
import { toolsDialogFactory } from './dialogs/tools.js';
import { handleForkCommand } from './extensions/session_commands/slash/fork/controller.js';
import { handleModelCommand } from './extensions/session_commands/slash/model/controller.js';
import { openResumeSelector } from './extensions/session_commands/slash/resume/controller.js';
import { openScopedModelsPanel } from './extensions/session_commands/slash/scoped-models/controller.js';
import { openTodosViewer } from './extensions/session_commands/slash/todos/controller.js';
import { handleTreeCommand } from './extensions/session_commands/slash/tree/controller.js';

export default function extension(api: ExtensionUIAPI): void {
  api.registerCommand('tree', {
    description: '导航会话树（无参数弹选择器）: /tree [target_id]',
    handler: async (args, ctx) => {
      await handleTreeCommand(args, ctx);
    },
  });
  api.registerCommand('todos', {
    description: '查看当前分支的 todo 清单（模态查看器）',
    handler: async (_args, ctx) => {
      await openTodosViewer(ctx);
    },
  });
  api.registerCommand('model', {
    description: '切换模型（当前模型置顶带 ✓；Tab 切 all/scoped 作用域）: /model [provider/id]',
    handler: async (args, ctx) => {
      await handleModelCommand(args, ctx);
    },
  });
  api.registerCommand('scoped-models', {
    description: 'scoped 模型池面板（ctrl+p 循环启用集与顺序；ctrl+s 保存）',
    handler: async (_args, ctx) => {
      await openScopedModelsPanel(ctx);
    },
  });
  api.registerCommand('resume', {
    description: '浏览并恢复已有会话（删除/重命名/作用域/排序/搜索）',
    handler: async (_args, ctx) => {
      await openResumeSelector(ctx);
    },
  });
  api.registerCommand('fork', {
    description: '从用户消息分叉会话（无参数时弹选择器；enter 回填原文编辑后重发）: /fork [entry_id] [at|before|after]',
    handler: async (args, ctx) => {
      await handleForkCommand(args, ctx);
    },
  });
  // question 工具的自定义对话框（dialog:question——注册即触发能力重宣告；
  // 后端按 has_capability 判定走单框或基线两步降级；多问形态同 slot 分派）
  api.registerDialog?.('question', questionDialogFactory);
  // tools 工具开关面板（dialog:tools）
  api.registerDialog?.('tools', toolsDialogFactory);
}
