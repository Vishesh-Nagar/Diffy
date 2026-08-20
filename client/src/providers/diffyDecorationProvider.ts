import * as vscode from 'vscode';
import { ServerClient } from '../serverClient';

export class DiffyDecorationProvider implements vscode.Disposable {
    private decorationType: vscode.TextEditorDecorationType;
    private _server: ServerClient;
    private _disposables: vscode.Disposable[] = [];
    private _debounceTimer: ReturnType<typeof setTimeout> | undefined;
    private static readonly DEBOUNCE_MS = 2000;

    constructor(backend: ServerClient) {
        this._server = backend;

        this.decorationType = vscode.window.createTextEditorDecorationType({
            backgroundColor: 'rgba(100, 200, 100, 0.1)',
            isWholeLine: true,
            gutterIconPath: vscode.Uri.parse('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><circle cx="4" cy="8" r="3" fill="%2389d185"/></svg>'),
            gutterIconSize: 'contain',
            overviewRulerColor: 'rgba(100, 200, 100, 0.5)',
            overviewRulerLane: vscode.OverviewRulerLane.Left,
        });

        this._disposables.push(
            vscode.window.onDidChangeActiveTextEditor(editor => {
                if (editor) {
                    this._scheduleUpdate(editor);
                }
            }),
            // Only update on save — NOT on every keystroke
            vscode.workspace.onDidSaveTextDocument(document => {
                const editor = vscode.window.activeTextEditor;
                if (editor && editor.document === document) {
                    this._scheduleUpdate(editor);
                }
            })
        );

        // Decorate immediately on the current editor
        if (vscode.window.activeTextEditor) {
            this._scheduleUpdate(vscode.window.activeTextEditor);
        }
    }

    private _scheduleUpdate(editor: vscode.TextEditor) {
        if (this._debounceTimer) {
            clearTimeout(this._debounceTimer);
        }
        this._debounceTimer = setTimeout(() => {
            this.updateDecorations(editor);
        }, DiffyDecorationProvider.DEBOUNCE_MS);
    }

    public async updateDecorations(editor: vscode.TextEditor) {
        if (!editor) { return; }

        const decorationsArray: vscode.DecorationOptions[] = [];

        try {
            const workspaceFolder = vscode.workspace.getWorkspaceFolder(editor.document.uri);
            if (!workspaceFolder) {
                editor.setDecorations(this.decorationType, []);
                return;
            }

            const repoPath = workspaceFolder.uri.fsPath;
            const filePath = vscode.workspace.asRelativePath(editor.document.uri, false);

            const result = await this._server.getRecentModifications(repoPath, filePath, 10);
            if (result && result.status === 'ok' && Array.isArray(result.lines)) {
                for (const line of result.lines) {
                    if (line > 0 && line <= editor.document.lineCount) {
                        const pos = new vscode.Position(line - 1, 0);
                        decorationsArray.push({
                            range: new vscode.Range(pos, pos),
                            hoverMessage: new vscode.MarkdownString('$(git-commit) Recent Git modification')
                        });
                    }
                }
            }

            editor.setDecorations(this.decorationType, decorationsArray);
        } catch (e) {
            // Silently ignore — backend may not be ready or file is untracked
        }
    }

    dispose() {
        this._disposables.forEach(d => d.dispose());
        this.decorationType.dispose();
        if (this._debounceTimer) {
            clearTimeout(this._debounceTimer);
        }
    }
}
