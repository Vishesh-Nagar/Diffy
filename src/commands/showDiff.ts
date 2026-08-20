import * as vscode from 'vscode';
import * as cp from 'child_process';
import * as path from 'path';

class GitContentProvider implements vscode.TextDocumentContentProvider {
    provideTextDocumentContent(uri: vscode.Uri): Promise<string> {
        return new Promise((resolve, reject) => {
            const query = JSON.parse(uri.query);
            const { repo, file, commit } = query;

            // Use execFile (not exec) to avoid shell injection
            cp.execFile('git', ['show', `${commit}:${file}`], { cwd: repo }, (err, stdout, stderr) => {
                if (err) {
                    if (stderr.includes('exists on disk, but not in') || stderr.includes('does not exist')) {
                        resolve(''); // File didn't exist at this commit
                    } else {
                        reject(new Error(`Git error: ${stderr.trim()}`));
                    }
                    return;
                }
                resolve(stdout);
            });
        });
    }
}

let providerRegistered = false;

export async function cmdShowDiff(repo: string, file: string, commit: string) {
    if (!providerRegistered) {
        vscode.workspace.registerTextDocumentContentProvider('diffy-git', new GitContentProvider());
        providerRegistered = true;
    }

    try {
        const oldUri = vscode.Uri.parse(`diffy-git:${file}?${JSON.stringify({ repo, file, commit: commit + '^' })}`);
        const newUri = vscode.Uri.parse(`diffy-git:${file}?${JSON.stringify({ repo, file, commit })}`);
        
        await vscode.commands.executeCommand('vscode.diff', oldUri, newUri, `Diff: ${file} (${commit})`);
    } catch (e: any) {
        vscode.window.showErrorMessage(`Failed to open diff: ${e.message}`);
    }
}
