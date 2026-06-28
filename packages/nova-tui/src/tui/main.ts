#!/usr/bin/env node
/**
 * Nova TUI 入口。
 *
 * 用法:
 *   nova                    # 在当前目录启动新会话
 *   nova -r <session-id>    # 恢复指定会话
 *   nova -r                 # 交互式选择会话
 *   nova -c                 # 继续上一个会话
 *   nova -m <model-alias>   # 指定模型
 *   nova pkg list           # 列出已安装的包
 *   nova pkg install <path> --kind <definition|tool>
 *   nova pkg uninstall <name> --kind <definition|tool>
 *   nova pkg info <name> --kind <definition|tool>
 */

import { Command, Option } from 'commander';
import { NovaTUI } from './app.js';
import { runPkgCli } from './pkg-cli.js';

interface CLIOptions {
  dir: string;
  python: string;
  serverModule: string;
  session: string | true | undefined;
  continue: boolean;
  model: string | undefined;
  agent: string | undefined;
}

// ------------------------------------------------------------------
// pkg subcommand builder
// ------------------------------------------------------------------
function buildPkgCommand(): Command {
  const pkg = new Command('pkg')
    .description('Package manager — install definitions and tools')
    .option('--python <path>', 'Python 可执行文件路径', 'python')
    .option('--server-module <module>', 'Python 服务器模块名', 'nova_harness.rpc');

  pkg
    .command('list')
    .description('列出已安装的包')
    .option('--kind <kind>', '按类型过滤 (definition 或 tool)')
    .action(async (cmdOpts, cmd) => {
      const globalOpts = cmd.parent?.opts() as { python: string; serverModule: string };
      const exitCode = await runPkgCli({
        command: 'list',
        args: { kind: cmdOpts.kind, python: globalOpts.python, serverModule: globalOpts.serverModule },
      });
      process.exit(exitCode);
    });

  pkg
    .command('install <source>')
    .description('从本地路径安装包')
    .requiredOption('--kind <kind>', '包类型 (definition / tool / agent)')
    .option('--name <name>', '目标名称（默认使用目录名）')
    .action(async (source: string, cmdOpts, cmd) => {
      const globalOpts = cmd.parent?.opts() as { python: string; serverModule: string };
      const exitCode = await runPkgCli({
        command: 'install',
        args: {
          source,
          kind: cmdOpts.kind,
          name: cmdOpts.name,
          python: globalOpts.python,
          serverModule: globalOpts.serverModule,
        },
      });
      process.exit(exitCode);
    });

  pkg
    .command('uninstall <name>')
    .description('卸载已安装的包')
    .requiredOption('--kind <kind>', '包类型 (definition / tool / agent)')
    .action(async (name: string, cmdOpts, cmd) => {
      const globalOpts = cmd.parent?.opts() as { python: string; serverModule: string };
      const exitCode = await runPkgCli({
        command: 'uninstall',
        args: {
          name,
          kind: cmdOpts.kind,
          python: globalOpts.python,
          serverModule: globalOpts.serverModule,
        },
      });
      process.exit(exitCode);
    });

  pkg
    .command('info <name>')
    .description('查看包详情')
    .requiredOption('--kind <kind>', '包类型 (definition / tool / agent)')
    .action(async (name: string, cmdOpts, cmd) => {
      const globalOpts = cmd.parent?.opts() as { python: string; serverModule: string };
      const exitCode = await runPkgCli({
        command: 'info',
        args: {
          name,
          kind: cmdOpts.kind,
          python: globalOpts.python,
          serverModule: globalOpts.serverModule,
        },
      });
      process.exit(exitCode);
    });

  return pkg;
}

// ------------------------------------------------------------------
// Main program
// ------------------------------------------------------------------
async function run(): Promise<void> {
  const program = new Command('nova')
    .description('Nova TUI — Terminal UI for Nova Harness')
    .version('0.1.0', '-V, --version')
    .helpOption('-h, --help', '显示帮助信息')
    .configureHelp({ helpWidth: 100 });

  program
    .addOption(
      new Option(
        '-r, --session [id]',
        '恢复已有会话。带 ID 恢复指定会话；不带 ID 交互式选择。',
      ).argParser((val: string | boolean) => (val === true ? '' : (val as string))),
    )
    .option('-c, --continue', '继续当前目录的上一个会话', false)
    .option('-m, --model <model>', '指定使用的模型别名')
    .option('-a, --agent <name>', '指定使用的 agent 定义')
    .option('-d, --dir <path>', '工作目录', process.cwd())
    .option('--python <path>', 'Python 可执行文件路径', 'python')
    .option('--server-module <module>', 'Python 服务器模块名', 'nova_harness.rpc');

  program.addCommand(buildPkgCommand());

  program.action(async () => {
    const opts = program.opts<CLIOptions>();

    // 参数冲突检查
    if (opts.continue && opts.session !== undefined) {
      program.error('错误: --continue 与 --session 不能同时使用');
    }

    const sessionFlag = opts.session === true ? '' : opts.session;
    const tui = new NovaTUI({
      workDir: opts.dir,
      version: '0.1.0',
      pythonPath: opts.python,
      serverModule: opts.serverModule,
      sessionFlag: sessionFlag,
      continueLast: opts.continue,
      model: opts.model,
      agentName: opts.agent,
    });

    tui.onExit = async (exitCode = 0) => {
      process.stdout.write('\n  Bye!\n');
      process.exit(exitCode);
    };

    try {
      await tui.start();
    } catch (error) {
      process.stderr.write(
        `启动 Nova TUI 失败: ${error instanceof Error ? error.message : String(error)}\n`,
      );
      process.exit(1);
    }
  });

  await program.parseAsync();
}

run().catch((error) => {
  process.stderr.write(`未处理的错误: ${error instanceof Error ? error.message : String(error)}\n`);
  process.exit(1);
});
