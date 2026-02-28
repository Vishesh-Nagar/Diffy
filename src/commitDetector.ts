/**
 * Diffy — Commit Detector
 * Event-driven commit detection using three layers:
 * 1. VS Code Git Extension API (repository state changes)
 * 2. Git hooks (post-commit/post-merge signal files)
 * 3. GitHub webhook notifications (forwarded from Python backend)
 * Fallback: check on window focus if no webhook configured.
 */

import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import { BackendClient } from './backendClient';

// Git Extension API types (simplified)
interface GitRepository {
    rootUri: vscode.Uri;
    state: {
        HEAD: { commit?: string; name?: string } | undefined;
        onDidChange: vscode.Event<void>;
    };
}

interface GitAPI {
    repositories: GitRepository[];
    onDidOpenRepository: vscode.Event<GitRepository>;
    onDidCloseRepository: vscode.Event<GitRepository>;
}

export class CommitDetector {
    private disposables: vscode.Disposable[] = [];
    private gitApi: GitAPI | null = null;
    private trackedRepos = new Map<string, {
        lastHash: string;
        watcher?: vscode.FileSystemWatcher;
    }>();
    private backend: BackendClient;
    private throttleTimers = new Map<string, NodeJS.Timeout>();
    private outputChannel: vscode.OutputChannel;

    // Event emitter for new commits
    private _onNewCommits = new vscode.EventEmitter<{
        repoPath: string;
        source: string;
    }>();
    public readonly onNewCommits = this._onNewCommits.event;

    constructor(backend: BackendClient, outputChannel: vscode.OutputChannel) {
        this.backend = backend;
        this.outputChannel = outputChannel;
    }

    /**
     * Initialize all three detection layers.
     */
    async activate(): Promise<void> {
        await this.initGitApiLayer();
        this.initFocusLayer();
        this.initWebhookLayer();

        this.outputChannel.appendLine('Commit detector activated');
    }

    /**
     * Clean up all watchers and listeners.
     */
    deactivate(): void {
        for (const d of this.disposables) { d.dispose(); }
        for (const [, info] of this.trackedRepos) {
            info.watcher?.dispose();
        }
        this.trackedRepos.clear();
        this._onNewCommits.dispose();
    }

    // ---------------------------------------------------------------
    // Layer 1: VS Code Git Extension API
    // ---------------------------------------------------------------

    private async initGitApiLayer(): Promise<void> {
        try {
            const gitExtension = vscode.extensions.getExtension('vscode.git');
            if (!gitExtension) {
                this.outputChannel.appendLine('Git extension not found');
                return;
            }

            if (!gitExtension.isActive) {
                await gitExtension.activate();
            }

            const gitExports = gitExtension.exports;
            this.gitApi = gitExports.getAPI(1) as GitAPI;

            // Track existing repositories
            for (const repo of this.gitApi.repositories) {
                this.trackRepository(repo);
            }

            // Track new repositories
            const openSub = this.gitApi.onDidOpenRepository((repo) => {
                this.trackRepository(repo);
            });
            this.disposables.push(openSub);

            const closeSub = this.gitApi.onDidCloseRepository((repo) => {
                this.untrackRepository(repo.rootUri.fsPath);
            });
            this.disposables.push(closeSub);

            this.outputChannel.appendLine(
                `Git API: tracking ${this.gitApi.repositories.length} repo(s)`
            );
        } catch (err: any) {
            this.outputChannel.appendLine(`Git API init error: ${err.message}`);
        }
    }

    private trackRepository(repo: GitRepository): void {
        const repoPath = repo.rootUri.fsPath;

        if (this.trackedRepos.has(repoPath)) { return; }

        const currentHash = repo.state.HEAD?.commit || '';

        // Listen for state changes (commits, checkouts, pulls)
        const stateSub = repo.state.onDidChange(() => {
            const newHash = repo.state.HEAD?.commit || '';
            const tracked = this.trackedRepos.get(repoPath);

            if (tracked && newHash && newHash !== tracked.lastHash) {
                tracked.lastHash = newHash;
                this.emitThrottled(repoPath, 'git-api');
            }
        });
        this.disposables.push(stateSub);

        // Layer 2: install git hooks and watch signal file
        const watcher = this.installGitHooks(repoPath);

        this.trackedRepos.set(repoPath, {
            lastHash: currentHash,
            watcher,
        });

        this.outputChannel.appendLine(`Tracking: ${repoPath}`);
    }

    private untrackRepository(repoPath: string): void {
        const info = this.trackedRepos.get(repoPath);
        if (info) {
            info.watcher?.dispose();
            this.trackedRepos.delete(repoPath);
        }
    }

    // ---------------------------------------------------------------
    // Layer 2: Git Hooks + Signal File
    // ---------------------------------------------------------------

    private installGitHooks(repoPath: string): vscode.FileSystemWatcher | undefined {
        const gitDir = path.join(repoPath, '.git');
        const hooksDir = path.join(gitDir, 'hooks');
        const signalFile = path.join(gitDir, '.diffpilot-signal');

        // Ensure hooks directory exists
        try {
            if (!fs.existsSync(hooksDir)) {
                fs.mkdirSync(hooksDir, { recursive: true });
            }
        } catch {
            return undefined;
        }

        // Install hooks that touch the signal file
        const hookNames = ['post-commit', 'post-merge', 'post-checkout'];
        const hookContent = process.platform === 'win32'
            ? `@echo off\necho %date% %time% > "${signalFile}"\n`
            : `#!/bin/sh\ndate > "${signalFile}"\n`;

        for (const hookName of hookNames) {
            const hookPath = path.join(hooksDir, hookName);
            try {
                // Only install if hook doesn't exist (don't overwrite user hooks)
                if (!fs.existsSync(hookPath)) {
                    fs.writeFileSync(hookPath, hookContent, { mode: 0o755 });
                } else {
                    // Append to existing hook if it doesn't already have our signal
                    const existing = fs.readFileSync(hookPath, 'utf-8');
                    if (!existing.includes('.diffpilot-signal')) {
                        const append = process.platform === 'win32'
                            ? `\necho %date% %time% > "${signalFile}"\n`
                            : `\ndate > "${signalFile}"\n`;
                        fs.appendFileSync(hookPath, append);
                    }
                }
            } catch {
                // Non-critical: hook installation is a backup layer
            }
        }

        // Watch the signal file
        try {
            const pattern = new vscode.RelativePattern(gitDir, '.diffpilot-signal');
            const watcher = vscode.workspace.createFileSystemWatcher(pattern);

            watcher.onDidChange(() => {
                this.emitThrottled(repoPath, 'git-hook');
            });
            watcher.onDidCreate(() => {
                this.emitThrottled(repoPath, 'git-hook');
            });

            return watcher;
        } catch {
            return undefined;
        }
    }

    // ---------------------------------------------------------------
    // Layer 3: GitHub Webhook Notifications
    // ---------------------------------------------------------------

    private initWebhookLayer(): void {
        // Listen for webhook/indexed notifications from the Python backend
        this.backend.setNotificationHandler((method, params) => {
            if (method === 'webhook/indexed') {
                this.outputChannel.appendLine(
                    `Webhook: indexed ${params.commits_indexed} commits from ${params.repo}`
                );
                // The backend already indexed the diffs, so we just notify
                // the UI to refresh status
                vscode.commands.executeCommand('diffy.status');
            }

            // Forward streaming notifications
            if (method.startsWith('stream/')) {
                // These are handled by the extension.ts query flow
            }
        });
    }

    // ---------------------------------------------------------------
    // Fallback: Check on window focus
    // ---------------------------------------------------------------

    private initFocusLayer(): void {
        const focusSub = vscode.window.onDidChangeWindowState((state) => {
            if (state.focused) {
                // Quick check: has any tracked repo's HEAD changed?
                for (const [repoPath] of this.trackedRepos) {
                    this.emitThrottled(repoPath, 'focus');
                }
            }
        });
        this.disposables.push(focusSub);
    }

    // ---------------------------------------------------------------
    // Throttled event emission
    // ---------------------------------------------------------------

    private emitThrottled(repoPath: string, source: string): void {
        const key = repoPath;

        // Throttle: max one event per repo per 5 seconds
        if (this.throttleTimers.has(key)) {
            return;
        }

        this.throttleTimers.set(key, setTimeout(() => {
            this.throttleTimers.delete(key);
        }, 5000));

        this.outputChannel.appendLine(`New commit detected [${source}]: ${repoPath}`);
        this._onNewCommits.fire({ repoPath, source });
    }
}
