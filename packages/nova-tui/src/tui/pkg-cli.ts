/**
 * `nova pkg` subcommand — package manager CLI (non-TUI mode).
 *
 * Calls the lightweight `nova-pkg` Python CLI with `--json`, parses stdout,
 * and renders results with chalk for a nicer terminal experience.
 */

import { spawn } from 'node:child_process';
import chalk from 'chalk';

interface PkgGlobalArgs {
  python: string;
  serverModule: string;
}

interface PkgListArgs extends PkgGlobalArgs {
  kind?: string;
}

interface PkgInstallArgs extends PkgGlobalArgs {
  source: string;
  kind: string;
  name?: string;
}

interface PkgUninstallArgs extends PkgGlobalArgs {
  name: string;
  kind: string;
}

interface PkgInfoArgs extends PkgGlobalArgs {
  name: string;
  kind: string;
}

export type PkgArgs =
  | { command: 'list'; args: PkgListArgs }
  | { command: 'install'; args: PkgInstallArgs }
  | { command: 'uninstall'; args: PkgUninstallArgs }
  | { command: 'info'; args: PkgInfoArgs };

async function runNovaPkgJson(args: string[]): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const proc = spawn('nova-pkg', ['--json', ...args], {
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    proc.stdout.on('data', (chunk: Buffer) => { stdout += chunk.toString('utf-8'); });
    proc.stderr.on('data', (chunk: Buffer) => { stderr += chunk.toString('utf-8'); });
    proc.on('error', reject);
    proc.on('close', (code) => {
      if (code !== 0) {
        reject(new Error(stderr.trim() || `nova-pkg exited with code ${String(code)}`));
        return;
      }
      try {
        resolve(JSON.parse(stdout.trim()));
      } catch {
        reject(new Error(`Invalid JSON from nova-pkg: ${stdout.trim()}`));
      }
    });
  });
}

function pad(str: string, width: number): string {
  return str.length >= width ? str.slice(0, width - 1) + '…' : str.padEnd(width, ' ');
}

async function runList(args: PkgListArgs): Promise<number> {
  const argv = ['list'];
  const views = (await runNovaPkgJson(argv)) as Record<string, {
    name: string;
    version: string;
    description: string;
    agents: Array<{ name: string; version: string }>;
    tools: Array<{ name: string; version: string }>;
  }>;

  const keys = Object.keys(views);
  if (keys.length === 0) {
    console.log(chalk.gray('No packages installed.'));
    return 0;
  }

  for (const key of keys) {
    const view = views[key];
    if (key === '(standalone)') {
      console.log(chalk.dim('\n' + '─'.repeat(60)));
      console.log(chalk.dim('Standalone packages'));
    } else {
      const ver = view.version ? chalk.yellow(` @ ${view.version}`) : '';
      console.log(chalk.bold.cyan(`\nBundle: ${view.name}`) + ver);
      if (view.description) {
        console.log(chalk.gray(`  ${view.description}`));
      }
    }

    const agents = view.agents.map((a) => chalk.blue(a.name)).join(', ') || chalk.gray('(none)');
    const tools = view.tools.map((t) => chalk.green(t.name)).join(', ') || chalk.gray('(none)');
    console.log(`  Agents: ${agents}`);
    console.log(`  Tools:  ${tools}`);
  }
  return 0;
}

async function runInstall(args: PkgInstallArgs): Promise<number> {
  const argv = ['install', args.source, '--kind', args.kind];
  if (args.name) argv.push('--name', args.name);
  const result = (await runNovaPkgJson(argv)) as {
    name: string;
    kind: string;
    version: string;
    install_path: string;
  };

  console.log(
    chalk.green('✔') +
      ' Installed ' +
      chalk.bold(result.name) +
      chalk.gray(` (${result.kind})`) +
      ' @ ' +
      chalk.yellow(result.version),
  );
  console.log(chalk.gray('  → ') + result.install_path);
  return 0;
}

async function runUninstall(args: PkgUninstallArgs): Promise<number> {
  const argv = ['uninstall', args.name, '--kind', args.kind];
  const result = (await runNovaPkgJson(argv)) as { ok: boolean };

  if (result.ok) {
    console.log(
      chalk.green('✔') + ' Uninstalled ' + chalk.bold(args.name) + chalk.gray(` (${args.kind})`),
    );
    return 0;
  }
  console.error(
    chalk.red('✘') + ' Package ' + chalk.bold(args.name) + chalk.gray(` (${args.kind})`) + ' not found.',
  );
  return 1;
}

async function runInfo(args: PkgInfoArgs): Promise<number> {
  const argv = ['info', args.name, '--kind', args.kind];
  const result = (await runNovaPkgJson(argv)) as {
    name: string;
    kind: string;
    version: string;
    description: string;
    author: string;
    source: string;
    installed_at: string;
    install_path: string;
    dependencies: string[];
  } | null;

  if (!result) {
    console.error(
      chalk.red('✘') + ' Package ' + chalk.bold(args.name) + chalk.gray(` (${args.kind})`) + ' not found.',
    );
    return 1;
  }

  console.log(chalk.bold.cyan(result.name) + chalk.gray(` (${result.kind})`));
  console.log();
  console.log('  ' + chalk.gray('Version:    ') + (result.version || chalk.gray('—')));
  console.log('  ' + chalk.gray('Description:') + ' ' + (result.description || chalk.gray('—')));
  console.log('  ' + chalk.gray('Author:     ') + (result.author || chalk.gray('—')));
  console.log('  ' + chalk.gray('Source:     ') + (result.source || chalk.gray('—')));
  console.log('  ' + chalk.gray('Installed:  ') + (result.installed_at || chalk.gray('—')));
  console.log('  ' + chalk.gray('Path:       ') + result.install_path);
  if (result.dependencies?.length) {
    console.log('  ' + chalk.gray('Deps:       ') + result.dependencies.join(', '));
  }
  return 0;
}

export async function runPkgCli(args: PkgArgs): Promise<number> {
  try {
    switch (args.command) {
      case 'list':
        return await runList(args.args);
      case 'install':
        return await runInstall(args.args);
      case 'uninstall':
        return await runUninstall(args.args);
      case 'info':
        return await runInfo(args.args);
      default:
        return 1;
    }
  } catch (err) {
    console.error(chalk.red('Error: ') + (err instanceof Error ? err.message : String(err)));
    return 1;
  }
}
