/**
 * Diffy — VS Code Extension Entry Point
 * Activates the backend, commit detector, and registers all commands.
 * Uses VS Code's native UI (InputBox, OutputChannel, QuickPick) for interaction.
 */

import * as vscode from 'vscode';
import { ServerClient } from './serverClient';
import { CommitDetector } from './commitDetector';
import { ChatViewProvider } from './providers/chatViewProvider';
import { DiffyCodeLensProvider } from './providers/diffyCodeLensProvider';
import { DiffyDecorationProvider } from './providers/diffyDecorationProvider';
import { cmdShowDiff } from './commands/showDiff';
import { cmdReviewCommit } from './commands/reviewCommit';

interface ConfigOption extends vscode.QuickPickItem {
    action?: string;
}

let server: ServerClient;
let commitDetector: CommitDetector;
let outputChannel: vscode.OutputChannel;
let statusBarItem: vscode.StatusBarItem;

export async function activate(context: vscode.ExtensionContext) {
    outputChannel = vscode.window.createOutputChannel('Diffy');
    outputChannel.appendLine('Diffy is activating...');

    // Create status bar item
    statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
    statusBarItem.text = '$(rocket) Diffy';
    statusBarItem.tooltip = 'Diffy — Git-Diff RAG Assistant';
    statusBarItem.command = 'diffy.askQuestion';
    statusBarItem.show();
    context.subscriptions.push(statusBarItem);

    // Start backend
    server = new ServerClient(context);
    const started = await server.start();
    if (started) {
        outputChannel.appendLine('Backend started successfully');
        statusBarItem.text = '$(rocket) Diffy ✓';
    } else {
        outputChannel.appendLine('Backend failed to start');
        statusBarItem.text = '$(rocket) Diffy ✗';
        vscode.window.showWarningMessage(
            'Diffy: Backend failed to start. Make sure Python 3.10+ is installed.'
        );
    }

    // Start commit detector
    commitDetector = new CommitDetector(context, server, outputChannel);
    await commitDetector.activate();

    // Auto-index on new commits
    const autoIndexConfig = vscode.workspace.getConfiguration('diffy').get<boolean>('autoIndex', true);
    if (autoIndexConfig) {
        commitDetector.onNewCommits(async ({ repoPath, source }) => {
            outputChannel.appendLine(`Auto-indexing ${repoPath} (triggered by ${source})`);
            try {
                const result = await server.index(repoPath);
                if (result.chunks_added > 0) {
                    statusBarItem.text = `$(rocket) Diffy (+${result.chunks_added})`;
                    setTimeout(() => { statusBarItem.text = '$(rocket) Diffy ✓'; }, 3000);
                }
                outputChannel.appendLine(
                    `Indexed: ${result.commits_indexed} commits, ${result.chunks_added} chunks`
                );
            } catch (err: any) {
                outputChannel.appendLine(`Auto-index error: ${err.message}`);
            }
        });
    }

    // Index current workspace state on file save
    context.subscriptions.push(
        vscode.workspace.onDidSaveTextDocument(async (document) => {
            const workspaceFolder = vscode.workspace.getWorkspaceFolder(document.uri);
            if (workspaceFolder) {
                const repoPath = workspaceFolder.uri.fsPath;
                const filePath = vscode.workspace.asRelativePath(document.uri, false);
                const content = document.getText();
                try {
                    await server.indexFile(repoPath, filePath, content);
                    statusBarItem.text = `$(rocket) Diffy ✓`;
                } catch (err: any) {
                    outputChannel.appendLine(`File index error: ${err.message}`);
                }
            }
        })
    );

    // Register Webview Provider
    const chatProvider = new ChatViewProvider(context.extensionUri, server);
    context.subscriptions.push(
        vscode.window.registerWebviewViewProvider(ChatViewProvider.viewType, chatProvider)
    );

    // Register CodeLens and Decoration Providers
    const codeLensProvider = new DiffyCodeLensProvider();
    context.subscriptions.push(
        vscode.languages.registerCodeLensProvider({ scheme: 'file' }, codeLensProvider)
    );

    const decorationProvider = new DiffyDecorationProvider(server);
    context.subscriptions.push(decorationProvider);
    
    // ---- Register Commands ----

    context.subscriptions.push(
        vscode.commands.registerCommand('diffy.askQuestion', cmdAskQuestion),
        vscode.commands.registerCommand('diffy.askQuestionContext', (uri, range, symbolName) => cmdAskQuestionContext(uri, range, symbolName, chatProvider)),
        vscode.commands.registerCommand('diffy.showDiff', cmdShowDiff),
        vscode.commands.registerCommand('diffy.reviewCommit', () => cmdReviewCommit(server)),
        vscode.commands.registerCommand('diffy.indexRepo', cmdIndexRepo),
        vscode.commands.registerCommand('diffy.status', cmdStatus),
        vscode.commands.registerCommand('diffy.clearIndex', cmdClearIndex),
        vscode.commands.registerCommand('diffy.selectModel', cmdSelectModel),
        vscode.commands.registerCommand('diffy.configure', cmdConfigure),
    );

    outputChannel.appendLine('Diffy activated');

    // Auto-index workspace repos on startup
    if (autoIndexConfig && vscode.workspace.workspaceFolders) {
        for (const folder of vscode.workspace.workspaceFolders) {
            try {
                outputChannel.appendLine(`Initial indexing: ${folder.uri.fsPath}`);
                const result = await server.index(folder.uri.fsPath);
                outputChannel.appendLine(
                    `  → ${result.commits_indexed} commits, ${result.chunks_added} chunks`
                );
            } catch (err: any) {
                outputChannel.appendLine(`  → Error: ${err.message}`);
            }
        }
    }
}

export function deactivate() {
    commitDetector?.deactivate();
    server?.stop();
    outputChannel?.appendLine('Diffy deactivated');
}

// ---------------------------------------------------------------------------
// Command: Ask a Question
// ---------------------------------------------------------------------------

async function cmdAskQuestion() {
    vscode.commands.executeCommand('diffy.chatView.focus');
}

async function cmdAskQuestionContext(uri: vscode.Uri, range: vscode.Range, symbolName: string, chatProvider: ChatViewProvider) {
    vscode.commands.executeCommand('diffy.chatView.focus');
    const question = `Can you explain the \`${symbolName}\` code block?`;
    await chatProvider.askQuestion(question);
}

// ---------------------------------------------------------------------------
// Command: Index Repository
// ---------------------------------------------------------------------------

async function cmdIndexRepo() {
    // If we have workspace folders, offer them
    const folders = vscode.workspace.workspaceFolders;
    let repoPath: string | undefined;

    if (folders && folders.length > 0) {
        if (folders.length === 1) {
            repoPath = folders[0].uri.fsPath;
        } else {
            const picked = await vscode.window.showQuickPick(
                folders.map(f => ({ label: f.name, detail: f.uri.fsPath })),
                { placeHolder: 'Select repository to index' }
            );
            repoPath = picked?.detail;
        }
    }

    if (!repoPath) {
        const uri = await vscode.window.showOpenDialog({
            canSelectFiles: false,
            canSelectFolders: true,
            canSelectMany: false,
            openLabel: 'Select Repository',
        });
        repoPath = uri?.[0]?.fsPath;
    }

    if (!repoPath) { return; }

    statusBarItem.text = '$(loading~spin) Indexing...';
    outputChannel.show(true);
    outputChannel.appendLine(`\nIndexing: ${repoPath}`);

    try {
        const result = await server.index(repoPath, true);
        outputChannel.appendLine(
            `✅ Indexed ${result.commits_indexed} commits, ${result.chunks_added} chunks`
        );
        vscode.window.showInformationMessage(
            `Diffy: Indexed ${result.commits_indexed} commits (${result.chunks_added} chunks)`
        );
    } catch (err: any) {
        outputChannel.appendLine(`❌ Index error: ${err.message}`);
        vscode.window.showErrorMessage(`Diffy: ${err.message}`);
    }

    statusBarItem.text = '$(rocket) Diffy ✓';
}

// ---------------------------------------------------------------------------
// Command: Show Status
// ---------------------------------------------------------------------------

async function cmdStatus() {
    try {
        const status = await server.status();
        outputChannel.show(true);
        outputChannel.appendLine(`\n${'═'.repeat(40)}`);
        outputChannel.appendLine('📊 Diffy Status');
        outputChannel.appendLine(`${'═'.repeat(40)}`);
        outputChannel.appendLine(`Ollama: ${status.ollama_available ? '✅ Connected' : '❌ Not available'}`);
        outputChannel.appendLine(`Webhook: ${status.webhook_running ? '✅ Running' : '⚪ Not running'}`);
        outputChannel.appendLine(`Indexed repos: ${status.indexed_repos}`);
        outputChannel.appendLine(`Total chunks: ${status.total_chunks}`);
        outputChannel.appendLine(`Vocabulary size: ${status.vocabulary_size}`);

        if (status.repos) {
            for (const [repoPath, info] of Object.entries<any>(status.repos)) {
                outputChannel.appendLine(`  📁 ${info.name} (${repoPath})`);
                outputChannel.appendLine(`     Last indexed: ${info.last_indexed || 'never'}`);
                outputChannel.appendLine(`     Last hash: ${info.last_hash || '?'}`);
            }
        }
        outputChannel.appendLine(`${'═'.repeat(40)}\n`);
    } catch (err: any) {
        vscode.window.showErrorMessage(`Diffy status error: ${err.message}`);
    }
}

// ---------------------------------------------------------------------------
// Command: Clear Index
// ---------------------------------------------------------------------------

async function cmdClearIndex() {
    const confirm = await vscode.window.showWarningMessage(
        'Clear all indexed data?',
        { modal: true },
        'Clear All'
    );

    if (confirm !== 'Clear All') { return; }

    try {
        await server.clearIndex();
        vscode.window.showInformationMessage('Diffy: Index cleared');
        outputChannel.appendLine('Index cleared');
    } catch (err: any) {
        vscode.window.showErrorMessage(`Diffy: ${err.message}`);
    }
}

// ---------------------------------------------------------------------------
// Command: Select Model
// ---------------------------------------------------------------------------

async function cmdSelectModel() {
    try {
        const result = await server.listModels();
        const models = result.models || [];

        if (models.length === 0) {
            vscode.window.showWarningMessage(
                'No Ollama models found. Run: ollama pull codellama'
            );
            return;
        }

        const items: vscode.QuickPickItem[] = models.map((m: any) => ({
            label: m.name as string,
            detail: `Size: ${(m.size / 1e9).toFixed(1)}GB`,
        }));
        const picked = await vscode.window.showQuickPick(items, {
            placeHolder: 'Select Ollama model',
        });

        if (picked) {
            await server.setConfig({ model: picked.label });
            vscode.window.showInformationMessage(`Diffy: Model set to ${picked.label}`);
        }
    } catch (err: any) {
        vscode.window.showErrorMessage(`Diffy: ${err.message}`);
    }
}

// ---------------------------------------------------------------------------
// Command: Configure
// ---------------------------------------------------------------------------

async function cmdConfigure() {
    const options: ConfigOption[] = [
        { label: '🔗 Ollama URL', detail: 'Set Ollama API endpoint' },
        { label: '🤖 Model', detail: 'Select LLM model', action: 'selectModel' },
        { label: '🔑 GitHub Token', detail: 'Set GitHub Personal Access Token' },
        { label: '🔌 Webhook Port', detail: 'Set webhook receiver port' },
        { label: '📊 Max Commits', detail: 'Set max commits to index per repo' },
        { label: '📋 Show Status', detail: 'View current status', action: 'status' },
    ];

    const picked = await vscode.window.showQuickPick<ConfigOption>(options, {
        placeHolder: 'Configure Diffy',
    });

    if (!picked) { return; }

    if (picked.action === 'selectModel') {
        await cmdSelectModel();
        return;
    }
    if (picked.action === 'status') {
        await cmdStatus();
        return;
    }

    if (picked.label.includes('Ollama URL')) {
        const url = await vscode.window.showInputBox({
            prompt: 'Ollama API URL',
            value: 'http://localhost:11434',
        });
        if (url) { await server.setConfig({ ollama_url: url }); }
    } else if (picked.label.includes('GitHub Token')) {
        vscode.window.showInformationMessage(
            'For security, please set your DIFFY_GITHUB_TOKEN in a local .env file in your project or Diffy backend directory, then restart the extension.',
            { modal: true }
        );
    } else if (picked.label.includes('Webhook Port')) {
        const port = await vscode.window.showInputBox({
            prompt: 'Webhook receiver port',
            value: '9417',
        });
        if (port) { await server.setConfig({ webhook_port: parseInt(port) }); }
    } else if (picked.label.includes('Max Commits')) {
        const max = await vscode.window.showInputBox({
            prompt: 'Max commits to index per repository',
            value: '200',
        });
        if (max) { await server.setConfig({ max_commits: parseInt(max) }); }
    }
}
