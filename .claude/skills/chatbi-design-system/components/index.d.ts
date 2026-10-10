import type * as React from 'react';
type Scope = 'platform' | 'team' | 'personal';
type Tone = 'neutral' | 'success' | 'danger' | 'warning' | 'info' | 'accent';
export interface IconProps { name: 'chat' | 'database' | 'table' | 'chart' | 'line' | 'shield' | 'lock' | 'check' | 'x' | 'alert' | 'info' | 'help' | 'play' | 'copy' | 'edit' | 'download' | 'thumbsUp' | 'thumbsDown' | 'search' | 'plus' | 'send' | 'users' | 'book' | 'sigma' | 'upload' | 'cpu' | 'log' | 'flask' | 'flag' | 'refresh' | 'loader' | 'eyeOff' | 'chevronDown' | 'more' | 'sparkle'; size?: 'md' | 'sm'; label?: string; className?: string }
export declare function Icon(props: IconProps): React.ReactElement;
export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> { variant?: 'primary' | 'secondary' | 'ghost' | 'danger'; size?: 'md' | 'sm'; icon?: IconProps['name'] }
export declare function Button(props: ButtonProps): React.ReactElement;
export interface BadgeProps { tone?: Tone; icon?: IconProps['name']; mono?: boolean; outline?: boolean; children?: React.ReactNode }
export declare function Badge(props: BadgeProps): React.ReactElement;
export interface ScopeTagProps { level?: Scope; title?: string; children?: React.ReactNode }
export declare function ScopeTag(props: ScopeTagProps): React.ReactElement;
export interface TabsProps { items: { id: string; label: string; icon?: IconProps['name']; count?: number }[]; value?: string; defaultValue?: string; onChange?: (id: string) => void; label?: string }
export declare function Tabs(props: TabsProps): React.ReactElement;
export interface SidebarProps { brand?: string; groups: { label: string; items: { id: string; label: string; icon?: IconProps['name']; count?: number }[] }[]; active?: string; onNavigate?: (id: string) => void; footer?: React.ReactNode }
export declare function Sidebar(props: SidebarProps): React.ReactElement;
export interface PanelProps { title?: string; eyebrow?: string; actions?: React.ReactNode; className?: string; style?: React.CSSProperties; children?: React.ReactNode }
export declare function Panel(props: PanelProps): React.ReactElement;
export interface TextFieldProps extends React.InputHTMLAttributes<HTMLInputElement> { label?: string; hint?: string; error?: string; icon?: IconProps['name']; suffix?: React.ReactNode }
export declare function TextField(props: TextFieldProps): React.ReactElement;
export interface ComposerProps { source?: string; hint?: string; placeholder?: string; defaultValue?: string; streaming?: boolean; onSubmit?: (text: string) => void }
export declare function Composer(props: ComposerProps): React.ReactElement;
export interface ChatMessageProps { role?: 'user' | 'assistant'; meta?: React.ReactNode; streaming?: boolean; children?: React.ReactNode }
export declare function ChatMessage(props: ChatMessageProps): React.ReactElement;
export interface ClarifyPromptProps { question: React.ReactNode; options: { label: string; hint?: string }[]; onPick?: (option: { label: string; hint?: string }) => void }
export declare function ClarifyPrompt(props: ClarifyPromptProps): React.ReactElement;
export interface ExplainCardProps { summary: React.ReactNode; metric?: { name: string; scope?: Scope; expr?: string }; terms?: { term: string; field: string; scope?: Scope }[]; filters?: string[]; timeRange?: string }
export declare function ExplainCard(props: ExplainCardProps): React.ReactElement;
export interface SqlBlockProps { sql: string; meta?: string; editable?: boolean; editing?: boolean; onRun?: (sql: string) => void }
export declare function SqlBlock(props: SqlBlockProps): React.ReactElement;
export interface ResultColumn { key: string; label: string; field?: string; type?: 'text' | 'number'; masked?: boolean; digits?: number }
export interface ResultTableProps { columns: ResultColumn[]; rows: Record<string, unknown>[]; total?: number; ms?: number; truncated?: number; maxHeight?: number }
export declare function ResultTable(props: ResultTableProps): React.ReactElement;
export interface ChartCardProps { title: string; data: { label: string; values: number[] }[]; series?: string[]; type?: 'bar' | 'line'; format?: (v: number) => string; height?: number; caption?: React.ReactNode }
export declare function ChartCard(props: ChartCardProps): React.ReactElement;
export interface FeedbackBarProps { defaultValue?: 'up' | 'down'; defaultOpen?: boolean }
export declare function FeedbackBar(props: FeedbackBarProps): React.ReactElement;
export interface CalloutProps { tone?: 'info' | 'warning' | 'danger' | 'success'; title?: React.ReactNode; icon?: IconProps['name']; actions?: React.ReactNode; children?: React.ReactNode }
export declare function Callout(props: CalloutProps): React.ReactElement;
export interface CheckListProps { items: { status?: 'pass' | 'fail' | 'warn'; label: React.ReactNode; detail?: React.ReactNode }[] }
export declare function CheckList(props: CheckListProps): React.ReactElement;
export interface TraceProps { steps: { status?: 'ok' | 'fail' | 'retry' | 'run' | 'wait'; label: React.ReactNode; time?: string }[] }
export declare function Trace(props: TraceProps): React.ReactElement;
export interface RunListProps { rows: { status: 'ok' | 'blocked' | 'error' | 'fixed' | 'clarify'; question: string; reason?: string; detail?: string; user?: string; id: string; time: string }[]; showUser?: boolean }
export declare function RunList(props: RunListProps): React.ReactElement;
