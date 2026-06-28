/**
 * TUI 状态定义与工厂函数。
 */

import { Container, ProcessTerminal, TUI } from '@earendil-works/pi-tui';

export type StreamingPhase = 'idle' | 'waiting' | 'thinking' | 'composing';

export interface AppState {
  model: string;
  workDir: string;
  sessionId: string;
  thinking: boolean;
  thinkingText: string;
  contextUsage: number;
  contextTokens: number;
  maxContextTokens: number;
  streamingPhase: StreamingPhase;
  version: string;
  agentName: string;
}

export interface TranscriptEntry {
  id: number;
  kind: 'user' | 'assistant' | 'tool_call' | 'thinking' | 'status';
  content: string;
  color?: string;
  detail?: string;
  toolName?: string;
  toolArgs?: Record<string, unknown>;
  toolResult?: string;
  toolError?: boolean;
}

export interface TUIState {
  ui: TUI;
  terminal: ProcessTerminal;
  transcriptContainer: Container;
  activityContainer: Container;
  editorContainer: Container;
  footerContainer: Container;
  appState: AppState;
  transcriptEntries: TranscriptEntry[];
  nextEntryId: number;
  activitySpinner: string;
}

const GUTTER = 2;

export function createTUIState(workDir: string, version: string): TUIState {
  const terminal = new ProcessTerminal();
  const ui = new TUI(terminal);

  const transcriptContainer = new Container();
  const activityContainer = new Container();
  const editorContainer = new Container();
  const footerContainer = new Container();

  const appState: AppState = {
    model: '',
    workDir,
    sessionId: '',
    thinking: false,
    thinkingText: '',
    contextUsage: 0,
    contextTokens: 0,
    maxContextTokens: 0,
    streamingPhase: 'idle',
    version,
    agentName: '',
  };

  return {
    ui,
    terminal,
    transcriptContainer: withGutter(transcriptContainer, GUTTER),
    activityContainer: withGutter(activityContainer, GUTTER),
    editorContainer: withGutter(editorContainer, GUTTER),
    footerContainer: withGutter(footerContainer, GUTTER),
    appState,
    transcriptEntries: [],
    nextEntryId: 1,
    activitySpinner: '',
  };
}

function withGutter(container: Container, _gutter: number): Container {
  // pi-tui Container 本身不直接支持 gutter，由组件内部处理缩进
  return container;
}
