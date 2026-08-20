/**
 * Diffy — Backend Client
 * Spawns the Python backend as a child process and communicates
 * via JSON-RPC over stdin/stdout.
 */

import * as cp from 'child_process';
import * as path from 'path';
import * as vscode from 'vscode';

export interface RpcResponse {
    jsonrpc: string;
    id?: number;
    result?: any;
    error?: { code: number; message: string };
    method?: string;
    params?: any;
}


export class BackendClient {
    private process: cp.ChildProcess | null = null;
    private requestId = 0;
    private pendingRequests = new Map<number, {
        resolve: (value: any) => void;
        reject: (reason: any) => void;
    }>();
    private buffer = '';
    public readonly onNotificationEvent = new vscode.EventEmitter<{method: string, params: any}>();
    private outputChannel: vscode.OutputChannel;

    constructor(private context: vscode.ExtensionContext) {
        this.outputChannel = vscode.window.createOutputChannel('Diffy Backend');
    }


    /**
     * Start the Python backend process.
     */
    async start(): Promise<boolean> {
        if (this.process) {
            return true;
        }

        const config = vscode.workspace.getConfiguration('diffy');
        let pythonPath = config.get<string>('pythonPath', '.venv/Scripts/python.exe');
        const backendDir = path.join(this.context.extensionPath, 'backend');
        const mainScript = path.join(backendDir, 'main.py');

        // Resolve relative python path against extension directory
        if (!path.isAbsolute(pythonPath)) {
            const resolved = path.join(this.context.extensionPath, pythonPath);
            const fs = require('fs');
            if (fs.existsSync(resolved)) {
                pythonPath = resolved;
            } else {
                // Cross-platform fallback: try bin/python for Linux/macOS
                const unixPath = path.join(this.context.extensionPath, '.venv', 'bin', 'python');
                if (fs.existsSync(unixPath)) {
                    pythonPath = unixPath;
                }
                // else keep original and let spawn handle the error
            }
        }

        this.outputChannel.appendLine(`Python path: ${pythonPath}`);

        return new Promise((resolve) => {
            try {
                this.process = cp.spawn(pythonPath, [mainScript], {
                    cwd: backendDir,
                    stdio: ['pipe', 'pipe', 'pipe'],
                    env: {
                        ...process.env,
                        PYTHONIOENCODING: 'utf-8',
                        DIFFY_OLLAMA_URL: config.get<string>('ollamaUrl', 'http://localhost:11434'),
                        DIFFY_MODEL: config.get<string>('model', 'codellama'),
                        DIFFY_WEBHOOK_PORT: String(config.get<number>('webhookPort', 9417)),
                        DIFFY_MAX_COMMITS: String(config.get<number>('maxCommits', 200)),
                        DIFFY_TOP_K: String(config.get<number>('topK', 5)),
                        DIFFY_EMBED_MODEL: config.get<string>('embedModel', 'nomic-embed-text'),
                    },
                });

                // Handle stdout (JSON-RPC responses)
                this.process.stdout?.on('data', (data: Buffer) => {
                    this.buffer += data.toString('utf-8');
                    this.processBuffer();
                });

                // Handle stderr (logs/errors)
                this.process.stderr?.on('data', (data: Buffer) => {
                    const msg = data.toString('utf-8').trim();
                    if (msg) {
                        this.outputChannel.appendLine(`[stderr] ${msg}`);
                    }
                });

                // Handle process exit
                this.process.on('exit', (code) => {
                    this.outputChannel.appendLine(`Backend exited with code ${code}`);
                    this.process = null;
                    // Reject all pending requests
                    for (const [, pending] of this.pendingRequests) {
                        pending.reject(new Error('Backend process exited'));
                    }
                    this.pendingRequests.clear();
                });

                this.process.on('error', (err) => {
                    this.outputChannel.appendLine(`Backend error: ${err.message}`);
                    this.process = null;
                    resolve(false);
                });

                // Wait for ready notification
                const readyTimeout = setTimeout(() => {
                    this.outputChannel.appendLine('Backend ready timeout — proceeding anyway');
                    resolve(true);
                }, 10000);

                const disposable = this.onNotificationEvent.event((msg) => {
                    if (msg.method === 'ready') {
                        clearTimeout(readyTimeout);
                        this.outputChannel.appendLine(
                            `Backend ready (v${msg.params?.version}, webhook port ${msg.params?.webhook_port})`
                        );
                        disposable.dispose();
                        resolve(true);
                    }
                });

            } catch (err: any) {
                this.outputChannel.appendLine(`Failed to start backend: ${err.message}`);
                resolve(false);
            }
        });
    }

    /**
     * Stop the backend process.
     */
    stop(): void {
        if (this.process) {
            this.process.kill();
            this.process = null;
        }
    }

    /**
     * Send a JSON-RPC request and wait for a response.
     */
    async request(method: string, params: any = {}): Promise<any> {
        if (!this.process) {
            throw new Error('Backend is not running');
        }

        const id = ++this.requestId;

        return new Promise((resolve, reject) => {
            this.pendingRequests.set(id, { resolve, reject });

            const request = {
                jsonrpc: '2.0',
                id,
                method,
                params,
            };

            const line = JSON.stringify(request) + '\n';
            this.process?.stdin?.write(line, 'utf-8');

            // Timeout after 120 seconds
            setTimeout(() => {
                if (this.pendingRequests.has(id)) {
                    this.pendingRequests.delete(id);
                    reject(new Error(`Request timeout: ${method}`));
                }
            }, 120000);
        });
    }

    /**
     * Process buffered stdout data, extracting complete JSON lines.
     */
    private processBuffer(): void {
        const lines = this.buffer.split('\n');
        // Keep the last incomplete line in the buffer
        this.buffer = lines.pop() || '';

        for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed) { continue; }

            try {
                const msg: RpcResponse = JSON.parse(trimmed);

                // Is it a response (has id)?
                if (msg.id !== undefined && msg.id !== null) {
                    const pending = this.pendingRequests.get(msg.id);
                    if (pending) {
                        this.pendingRequests.delete(msg.id);
                        if (msg.error) {
                            pending.reject(new Error(msg.error.message));
                        } else {
                            pending.resolve(msg.result);
                        }
                    }
                }
                // Is it a notification (has method, no id)?
                else if (msg.method) {
                    this.onNotificationEvent.fire({ method: msg.method, params: msg.params || {} });
                }
            } catch {
                this.outputChannel.appendLine(`[parse error] ${trimmed.substring(0, 200)}`);
            }
        }
    }

    /**
     * Check if the backend is running.
     */
    isRunning(): boolean {
        return this.process !== null;
    }

    // ----- Convenience methods -----

    async index(repoPath: string, force = false): Promise<any> {
        return this.request('index', { repoPath, force });
    }

    async query(question: string, model?: string): Promise<any> {
        return this.request('query', { question, model });
    }

    async queryStream(question: string, model?: string): Promise<any> {
        return this.request('queryStream', { question, model });
    }

    async retrieve(question: string, topK?: number): Promise<any> {
        return this.request('retrieve', { question, topK });
    }

    async status(): Promise<any> {
        return this.request('status');
    }

    async listRepos(): Promise<any> {
        return this.request('listRepos');
    }

    async listModels(): Promise<any> {
        return this.request('listModels');
    }

    async clearIndex(repoPath?: string): Promise<any> {
        return this.request('clearIndex', { repoPath });
    }

    async setConfig(config: Record<string, any>): Promise<any> {
        return this.request('setConfig', config);
    }

    async indexDiffs(data: any): Promise<any> {
        return this.request('indexDiffs', data);
    }

    async indexFile(repoPath: string, filePath: string, content: string): Promise<any> {
        return this.request('indexFile', { repoPath, filePath, content });
    }

    async reviewCommits(repoPath: string, numCommits: number = 5): Promise<any> {
        return this.request('reviewCommits', { repoPath, numCommits });
    }

    async getRecentModifications(repoPath: string, filePath: string, limit: number = 10): Promise<any> {
        return this.request('getRecentModifications', { repoPath, filePath, limit });
    }
}
