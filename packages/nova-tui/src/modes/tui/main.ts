#!/usr/bin/env node
/**
 * nova —— Nova TUI 入口（M1 薄壳）。
 *
 * 启动 NovaUIRuntime（spawn Python 后端、握手、createSession、全量同步），
 * 然后进入 pi-tui 主循环。薄壳原则：不做任何会话逻辑/呈现归约——
 * 状态来自 runtime.store，命令经 runtime.invoke，对话框经 onUIRequest。
 *
 * CLI flags：解析/校验/@file 展开的全部逻辑
 * 拆在 controllers/startup.ts（纯函数可单测）；本文件只做 commander
 * 接线与透传。StartupFlags 字段（sessionFile/thinking/resume/sessionName/
 * noSession）随 options 透传给 app——NovaTuiAppOptions 扩展后生效
 * （app.ts 装配点，见交付报告）。
 */

import { Command } from 'commander';

import { NOVA_VERSION } from '../../version.js';
import { NovaTuiApp, type NovaTuiAppOptions } from './app.js';

// 进程名（终端选项卡/任务管理器显示，否则只显示 node）
process.title = 'nova';

import {
  buildInitialMessage,
  expandFileArguments,
  extractExtensionFlags,
  isValidThinkingLevel,
  resolveSessionArg,
  splitMessageTokens,
  StartupError,
  VALID_THINKING_LEVELS,
  type StartupFlags,
} from './controllers/startup.js';

const program = new Command();

program
  .name('nova')
  .description('Nova TUI（NovaUIRuntime + pi-tui 薄壳）')
  // 版本戳：编译态为构建期注入值，node/tsx 态为 package.json——同一来源
  .version(NOVA_VERSION)
  .argument('[message...]', '启动后立即发送的首条消息（@file 展开为文件文本）')
  .option('-c, --cwd <dir>', '工作目录', process.cwd())
  .option('-m, --model <ref>', '模型（provider/id）')
  .option('-a, --agent <name>', 'Agent 名称')
  .option('--continue', '继续当前目录最近一次会话')
  .option('-r, --resume', '启动后打开会话选择器')
  .option('--session <file|id>', '恢复指定会话（文件路径或会话 id——裸 id 由后端在 cwd 会话目录解析）')
  .option('-n, --name <name>', '设置会话名（启动后 setSessionName）')
  .option('--thinking <level>', `思考级别（${VALID_THINKING_LEVELS.join('/')}）`)
  .option('--no-session', '不持久化会话（内存态，不落盘不进会话列表）')
  .action(async (messageParts: string[], opts) => {
    const cwd = opts.cwd as string;
    const continueLast = Boolean(opts.continue);
    const resume = Boolean(opts.resume);
    // commander 的 --session <v> 与 --no-session 共享 attribute：string / false / undefined 三态
    const sessionFile =
      typeof opts.session === 'string' ? resolveSessionArg(opts.session as string, cwd) : undefined;
    const noSession = opts.session === false;

    // —— 互斥校验（—报错退出）——
    const conflicts = [
      sessionFile !== undefined ? '--session' : undefined,
      continueLast ? '--continue' : undefined,
      resume ? '--resume' : undefined,
    ].filter((flag): flag is string => flag !== undefined);
    if (conflicts.length > 1) {
      console.error(`错误：${conflicts.join(' 与 ')} 互斥，请只选一个`);
      process.exit(1);
    }

    // --name 非空校验
    const sessionName = typeof opts.name === 'string' ? (opts.name as string).trim() : undefined;
    if (opts.name !== undefined && !sessionName) {
      console.error('错误：--name 需要非空值');
      process.exit(1);
    }

    // --thinking 校验（非法值警告并忽略）
    let thinking: string | undefined;
    if (typeof opts.thinking === 'string') {
      if (isValidThinkingLevel(opts.thinking)) {
        thinking = opts.thinking;
      } else {
        console.error(
          `警告：非法思考级别 "${opts.thinking as string}"（合法值：${VALID_THINKING_LEVELS.join(', ')}）——已忽略`,
        );
      }
    }

    // —— @file 展开进首条消息（文本内联 + 图片附件）——
    const { messageTokens, fileArgs } = splitMessageTokens(messageParts);
    let initialMessage: string | undefined;
    let initialImages: Array<{ type: 'image'; data: string; mimeType: string }> = [];
    try {
      const expanded = fileArgs.length > 0 ? await expandFileArguments(fileArgs, cwd) : { text: '', images: [] };
      initialMessage = buildInitialMessage(expanded.text, messageTokens);
      initialImages = expanded.images;
    } catch (error) {
      if (error instanceof StartupError) {
        console.error(`错误：${error.message}`);
        process.exit(1);
      }
      throw error;
    }

    const startupFlags: StartupFlags = {
      ...(sessionFile !== undefined ? { sessionFile } : {}),
      ...(thinking !== undefined ? { thinking } : {}),
      ...(resume ? { resume: true } : {}),
      ...(sessionName !== undefined ? { sessionName } : {}),
      ...(noSession ? { noSession: true } : {}),
      ...(Object.keys(extensionFlags).length > 0 ? { extensionFlags } : {}),
    };
    // StartupFlags 字段随 options 透传（NovaTuiAppOptions 扩展后由 app 消费——装配点）
    const appOptions: NovaTuiAppOptions & StartupFlags = {
      cwd,
      model: opts.model as string | undefined,
      agentName: opts.agent as string | undefined,
      continueLast,
      initialMessage,
      initialImages,
      ...startupFlags,
    };
    const app = new NovaTuiApp(appOptions);
    await app.run();
  });

// 扩展 flag 透传：解析先于后端装配（合法集在注册表），未声明的长选项
// 宽松收集后随 createSession 上行，装配期校验报错。已知集从 commander
// 已注册选项派生，不另维护平行清单
const knownLongs = new Set(
  program.options
    .map((option) => option.long?.replace(/^--/, ''))
    .filter((name): name is string => Boolean(name)),
);
const { rest: argvRest, extensionFlags } = extractExtensionFlags(
  process.argv.slice(2),
  knownLongs,
);

await program.parseAsync([...process.argv.slice(0, 2), ...argvRest]);
