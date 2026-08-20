import * as vscode from 'vscode';
import { ServerClient } from '../serverClient';

export async function cmdReviewCommit(server: ServerClient) {
    const folders = vscode.workspace.workspaceFolders;
    let repoPath: string | undefined;

    if (folders && folders.length > 0) {
        if (folders.length === 1) {
            repoPath = folders[0].uri.fsPath;
        } else {
            const picked = await vscode.window.showQuickPick(
                folders.map(f => ({ label: f.name, detail: f.uri.fsPath })),
                { placeHolder: 'Select repository to review' }
            );
            repoPath = picked?.detail;
        }
    }

    if (!repoPath) {
        return;
    }

    const numCommitsStr = await vscode.window.showInputBox({
        prompt: 'How many recent commits to review?',
        value: '5',
        validateInput: (value) => isNaN(parseInt(value)) ? 'Please enter a valid number' : null
    });

    if (!numCommitsStr) {
        return;
    }

    const numCommits = parseInt(numCommitsStr);

    return vscode.window.withProgress({
        location: vscode.ProgressLocation.Notification,
        title: `Diffy: Reviewing last ${numCommits} commits...`,
        cancellable: false
    }, async (progress) => {
        try {
            const result = await server.reviewCommits(repoPath!, numCommits);
            
            if (result.status === 'ok') {
                const panel = vscode.window.createWebviewPanel(
                    'diffyReview',
                    'Diffy: Code Review',
                    vscode.ViewColumn.One,
                    { enableScripts: true }
                );

                // Escape review text for safe embedding into a JS string literal
                const escapedReview = JSON.stringify(result.review);

                panel.webview.html = `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Code Review</title>
    <script src="https://cdn.jsdelivr.net/npm/marked@9/marked.min.js"></script>
    <style>
        body { padding: 24px; font-family: var(--vscode-font-family); line-height: 1.7; color: var(--vscode-editor-foreground); background: var(--vscode-editor-background); }
        h1,h2,h3 { color: var(--vscode-textLink-foreground); border-bottom: 1px solid var(--vscode-panel-border); padding-bottom: 4px; }
        pre { background: var(--vscode-textBlockQuote-background); padding: 12px; border-radius: 6px; overflow-x: auto; }
        code { font-family: var(--vscode-editor-font-family); font-size: 0.9em; background: var(--vscode-textBlockQuote-background); padding: 1px 4px; border-radius: 3px; }
        pre code { background: none; padding: 0; }
        blockquote { border-left: 3px solid var(--vscode-textLink-foreground); margin: 0; padding-left: 12px; color: var(--vscode-descriptionForeground); }
        ul, ol { padding-left: 20px; }
    </style>
</head>
<body>
    <h2>AI Code Review — Last ${numCommits} Commits</h2>
    <div id="content"></div>
    <script>
        document.getElementById('content').innerHTML = marked.parse(${escapedReview});
    </script>
</body>
</html>`;
            } else {
                vscode.window.showErrorMessage(`Diffy Review Error: ${result.message}`);
            }
        } catch (err: any) {
            vscode.window.showErrorMessage(`Diffy Review Error: ${err.message}`);
        }
    });
}
