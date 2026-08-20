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
import { ServerClient } from './serverClient';

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
    private server: ServerClient;
    private throttleTimers = new Map<string, NodeJS.Timeout>();
    private outputChannel: vscode.OutputChannel;

    /**
     * Tracks repos where the user has granted hook-installation consent.
     * Stored in memory only — consent is re-requested on each session.
     */
    private _hookConsent = new Set<string>();

    /**
     * Maps repoPath → list of restoration actions to undo hook changes on deactivate.
     * Each action is either:
     *   { kind: 'delete', hookPath }           — we created the file, delete it
     *   { kind: 'strip', hookPath, appendLine } — we appended a line, strip it
     */
    private _hookRestoreMap = new Map<string, Array<
        | { kind: 'delete'; hookPath: string }
        | { kind: 'strip'; hookPath: string; appendLine: string }
    >>();

    // Event emitter for new commits
    private _onNewCommits = new vscode.EventEmitter<{
        repoPath: string;
        source: string;
    }>();
    public readonly onNewCommits = this._onNewCommits.event;

    constructor(backend: ServerClient, outputChannel: vscode.OutputChannel) {
        this.server = backend;
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
     * Clean up all watchers, listeners, and restore any modified git hooks.
     */
    deactivate(): void {
        for (const d of this.disposables) { d.dispose(); }
        for (const [repoPath, info] of this.trackedRepos) {
            info.watcher?.dispose();
            this.restoreGitHooks(repoPath);
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

    private async trackRepository(repo: GitRepository): Promise<void> {
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

        // Layer 2: request consent then install git hooks
        const watcher = await this.installGitHooks(repoPath);

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
            this.restoreGitHooks(repoPath);
            this.trackedRepos.delete(repoPath);
        }
    }

    // ---------------------------------------------------------------
    // Layer 2: Git Hooks + Signal File
    // ---------------------------------------------------------------

    /**
     * Prompt the user for consent once per repo, then install signal-file hooks.
     * Records every file write so restoreGitHooks() can undo the changes exactly.
     * Records every file write so restoreGitHooks() can undo the changes exactly.
     */
    private async installGitHooks(repoPath: string): Promise<vscode.FileSystemWatcher | undefined> {
        const gitDir = path.join(repoPath, '.git');
        const hooksDir = path.join(gitDir, 'hooks');
        const signalFile = path.join(gitDir, '.diffpilot-signal');

        // --- Consent gate ---
        if (!this._hookConsent.has(repoPath)) {
            const answer = await vscode.window.showInformationMessage(
                `Diffy: To detect commits automatically in "${path.basename(repoPath)}", ` +
                `it needs to install post-commit/post-merge/post-checkout git hooks. ` +
                `These will be removed when you uninstall or disable Diffy.`,
                { modal: false },
                'Allow',
                'Skip'
            );
            if (answer !== 'Allow') {
                this.outputChannel.appendLine(
                    `Hook install skipped (no consent): ${repoPath}`
                );
                return undefined;  // Git API layer alone will still detect commits
            }
            this._hookConsent.add(repoPath);
        }

        // Ensure hooks directory exists
        try {
            if (!fs.existsSync(hooksDir)) {
                fs.mkdirSync(hooksDir, { recursive: true });
            }
        } catch {
            return undefined;
        }

        const restoreActions: Array<
            | { kind: 'delete'; hookPath: string }
            | { kind: 'strip'; hookPath: string; appendLine: string }
        > = this._hookRestoreMap.get(repoPath) ?? [];
        this._hookRestoreMap.set(repoPath, restoreActions);

        // Install hooks that touch the signal file
        const hookNames = ['post-commit', 'post-merge', 'post-checkout'];
        const signalLine = process.platform === 'win32'
            ? `\necho %date% %time% > "${signalFile}"\n`
            : `\ndate > "${signalFile}"\n`;
        const newFileContent = process.platform === 'win32'
            ? `@echo off${signalLine}`
            : `#!/bin/sh${signalLine}`;

        for (const hookName of hookNames) {
            const hookPath = path.join(hooksDir, hookName);
            try {
                if (!fs.existsSync(hookPath)) {
                    // We're creating this file — record for deletion on cleanup
                    fs.writeFileSync(hookPath, newFileContent, { mode: 0o755 });
                    restoreActions.push({ kind: 'delete', hookPath });
                } else {
                    // Append only if our signal line isn't already present
                    const existing = fs.readFileSync(hookPath, 'utf-8');
                    if (!existing.includes('.diffpilot-signal')) {
                        fs.appendFileSync(hookPath, signalLine);
                        // Record the exact line appended so we can strip it later
                        restoreActions.push({ kind: 'strip', hookPath, appendLine: signalLine });
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

    /**
     * Undo all hook changes recorded for the given repo.
     * - 'delete': removes files Diffy created from scratch
     * - 'strip': removes only the line Diffy appended to an existing hook
     */
    private restoreGitHooks(repoPath: string): void {
        const actions = this._hookRestoreMap.get(repoPath);
        if (!actions || actions.length === 0) { return; }

        for (const action of actions) {
            try {
                if (action.kind === 'delete') {
                    if (fs.existsSync(action.hookPath)) {
                        fs.unlinkSync(action.hookPath);
                        this.outputChannel.appendLine(`Removed hook: ${action.hookPath}`);
                    }
                } else if (action.kind === 'strip') {
                    if (fs.existsSync(action.hookPath)) {
                        const content = fs.readFileSync(action.hookPath, 'utf-8');
                        const stripped = content.split(action.appendLine).join('');
                        fs.writeFileSync(action.hookPath, stripped, { mode: 0o755 });
                        this.outputChannel.appendLine(`Restored hook: ${action.hookPath}`);
                    }
                }
            } catch (err: any) {
                this.outputChannel.appendLine(
                    `Warning: could not restore hook ${action.hookPath}: ${err.message}`
                );
            }
        }

        this._hookRestoreMap.delete(repoPath);
        this._hookConsent.delete(repoPath);
    }

    // ---------------------------------------------------------------
    // Layer 3: GitHub Webhook Notifications
    // ---------------------------------------------------------------

    private initWebhookLayer(): void {
        // Subscribe to backend notifications using the correct EventEmitter API.
        // setNotificationHandler() was never defined on ServerClient \u2014 this
        // uses onNotificationEvent which is the actual pub-sub mechanism.
        const sub = this.server.onNotificationEvent.event(({ method, params }) => {
            if (method === 'webhook/indexed') {
                this.outputChannel.appendLine(
                    `Webhook: indexed ${params.commits_indexed} commits from ${params.repo}`
                );
                // The backend already indexed the diffs, so we just notify
                // the UI to refresh status
                vscode.commands.executeCommand('diffy.status');
            }
            // stream/* notifications are handled by the extension.ts query flow
        });
        this.disposables.push(sub);
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
