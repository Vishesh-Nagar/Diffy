import * as vscode from 'vscode';
import { ServerClient } from '../serverClient';

export class ChatViewProvider implements vscode.WebviewViewProvider {
    public static readonly viewType = 'diffy.chatView';
    private _view?: vscode.WebviewView;

    constructor(
        private readonly _extensionUri: vscode.Uri,
        private readonly _server: ServerClient
    ) {}

    public resolveWebviewView(
        webviewView: vscode.WebviewView,
        context: vscode.WebviewViewResolveContext,
        _token: vscode.CancellationToken,
    ) {
        this._view = webviewView;

        webviewView.webview.options = {
            enableScripts: true,
            localResourceRoots: [this._extensionUri]
        };

        webviewView.webview.html = this._getHtmlForWebview(webviewView.webview);

        webviewView.webview.onDidReceiveMessage(async (data) => {
            switch (data.type) {
                case 'askQuestion': {
                    const question = data.value;
                    if (!question) { return; }
                    await this.askQuestion(question);
                    break;
                }
                case 'openFile': {
                    const uri = vscode.Uri.file(data.value.file);
                    vscode.window.showTextDocument(uri);
                    break;
                }
                case 'showDiff': {
                    const { repo, file, commit } = data.value;
                    if (commit && commit !== 'LOCAL' && commit !== 'WORKSPACE') {
                        vscode.commands.executeCommand('diffy.showDiff', repo, file, commit);
                    } else {
                        const uri = vscode.Uri.file(repo + '/' + file);
                        vscode.window.showTextDocument(uri);
                    }
                    break;
                }
            }
        });
    }

    public async askQuestion(question: string) {
        if (!this._view) { return; }

        // Disable input while streaming
        this._view.webview.postMessage({ type: 'setLoading', loading: true });
        this._view.webview.postMessage({ type: 'addMessage', role: 'user', content: question });
        this._view.webview.postMessage({ type: 'addMessage', role: 'assistant', content: '' });

        try {
            const disposable = this._server.onNotificationEvent.event((msg) => {
                const { method, params } = msg;
                if (method === 'stream/context') {
                    this._view?.webview.postMessage({ type: 'context', context: params.context || [] });
                } else if (method === 'stream/chunk') {
                    this._view?.webview.postMessage({ type: 'appendChunk', chunk: params.text });
                } else if (method === 'stream/done') {
                    this._view?.webview.postMessage({ type: 'done' });
                    this._view?.webview.postMessage({ type: 'setLoading', loading: false });
                    disposable.dispose();
                } else if (method === 'stream/error') {
                    this._view?.webview.postMessage({ type: 'error', error: params.error });
                    this._view?.webview.postMessage({ type: 'setLoading', loading: false });
                    disposable.dispose();
                }
            });

            await this._server.queryStream(question);
        } catch (err: any) {
            this._view.webview.postMessage({ type: 'error', error: err.message });
            this._view.webview.postMessage({ type: 'setLoading', loading: false });
        }
    }

    private _getHtmlForWebview(webview: vscode.Webview) {
        // Use CDN so toolkit loads correctly even in packaged VSIX
        const toolkitUri = 'https://cdn.jsdelivr.net/npm/@vscode/webview-ui-toolkit@1.4.0/dist/toolkit.min.js';

        return `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <script type="module" src="${toolkitUri}"></script>
    <style>
        body { padding: 10px; font-family: var(--vscode-font-family); display: flex; flex-direction: column; height: 100vh; box-sizing: border-box; margin: 0; }
        .chat-container { display: flex; flex-direction: column; flex: 1; overflow: hidden; }
        .messages { flex: 1; overflow-y: auto; margin-bottom: 10px; display: flex; flex-direction: column; gap: 10px; }
        .message { padding: 8px 10px; border-radius: 6px; max-width: 90%; word-wrap: break-word; white-space: pre-wrap; }
        .message.user { background: var(--vscode-editor-inactiveSelectionBackground); align-self: flex-end; }
        .message.assistant { background: var(--vscode-editor-selectionBackground); align-self: flex-start; }
        .context-chips { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 6px; }
        .chip { font-size: 0.78em; padding: 2px 8px; background: var(--vscode-badge-background); color: var(--vscode-badge-foreground); border-radius: 10px; cursor: pointer; border: none; }
        .chip:hover { opacity: 0.75; }
        .input-container { display: flex; gap: 6px; align-items: flex-end; }
        vscode-text-area { flex-grow: 1; }
        .spinner { display: inline-block; width: 10px; height: 10px; border: 2px solid var(--vscode-badge-foreground); border-top-color: transparent; border-radius: 50%; animation: spin 0.7s linear infinite; margin-right: 6px; vertical-align: middle; }
        @keyframes spin { to { transform: rotate(360deg); } }
    </style>
</head>
<body>
    <div class="chat-container">
        <div class="messages" id="messages"></div>
        <div class="input-container">
            <vscode-text-area id="question-input" placeholder="Ask Diffy..." resize="vertical" rows="2"></vscode-text-area>
            <vscode-button id="ask-button">Ask</vscode-button>
        </div>
    </div>
    <script>
        const vscode = acquireVsCodeApi();
        const messagesContainer = document.getElementById('messages');
        const questionInput = document.getElementById('question-input');
        const askButton = document.getElementById('ask-button');

        let currentAssistantMessage = null;
        let isLoading = false;

        function setLoading(loading) {
            isLoading = loading;
            askButton.disabled = loading;
            questionInput.disabled = loading;
        }

        function sendQuestion() {
            if (isLoading) { return; }
            const text = questionInput.value.trim();
            if (text) {
                vscode.postMessage({ type: 'askQuestion', value: text });
                questionInput.value = '';
            }
        }

        askButton.addEventListener('click', sendQuestion);

        // Allow Ctrl+Enter or Shift+Enter to submit
        questionInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendQuestion();
            }
        });

        window.addEventListener('message', event => {
            const message = event.data;
            switch (message.type) {
                case 'setLoading':
                    setLoading(message.loading);
                    break;

                case 'addMessage': {
                    const msgDiv = document.createElement('div');
                    msgDiv.className = 'message ' + message.role;
                    if (message.role === 'assistant' && !message.content) {
                        const spinner = document.createElement('span');
                        spinner.className = 'spinner';
                        spinner.id = 'active-spinner';
                        msgDiv.appendChild(spinner);
                    } else {
                        msgDiv.textContent = message.content;
                    }
                    messagesContainer.appendChild(msgDiv);
                    if (message.role === 'assistant') {
                        currentAssistantMessage = msgDiv;
                    }
                    messagesContainer.scrollTop = messagesContainer.scrollHeight;
                    break;
                }

                case 'appendChunk':
                    if (currentAssistantMessage) {
                        // Remove spinner on first chunk
                        const spinner = document.getElementById('active-spinner');
                        if (spinner) { spinner.remove(); }
                        currentAssistantMessage.textContent += message.chunk;
                        messagesContainer.scrollTop = messagesContainer.scrollHeight;
                    }
                    break;

                case 'context':
                    if (currentAssistantMessage) {
                        const chipsDiv = document.createElement('div');
                        chipsDiv.className = 'context-chips';
                        message.context.forEach(ctx => {
                            const chip = document.createElement('button');
                            chip.className = 'chip';
                            chip.textContent = ctx.file + (ctx.commit ? ' @' + ctx.commit : '');
                            chip.title = ctx.message || '';
                            chip.onclick = () => {
                                vscode.postMessage({ type: 'showDiff', value: { repo: ctx.repo, file: ctx.file, commit: ctx.commit } });
                            };
                            chipsDiv.appendChild(chip);
                        });
                        currentAssistantMessage.appendChild(chipsDiv);
                        messagesContainer.scrollTop = messagesContainer.scrollHeight;
                    }
                    break;

                case 'done':
                    currentAssistantMessage = null;
                    break;

                case 'error': {
                    const spinner = document.getElementById('active-spinner');
                    if (spinner) { spinner.remove(); }
                    const errDiv = document.createElement('div');
                    errDiv.className = 'message assistant';
                    errDiv.style.color = 'var(--vscode-errorForeground)';
                    errDiv.textContent = 'Error: ' + message.error;
                    messagesContainer.appendChild(errDiv);
                    currentAssistantMessage = null;
                    break;
                }
            }
        });
    </script>
</body>
</html>`;
    }
}
