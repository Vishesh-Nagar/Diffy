import * as vscode from 'vscode';

export class DiffyCodeLensProvider implements vscode.CodeLensProvider {
    private _onDidChangeCodeLenses: vscode.EventEmitter<void> = new vscode.EventEmitter<void>();
    public readonly onDidChangeCodeLenses: vscode.Event<void> = this._onDidChangeCodeLenses.event;

    constructor() {
        vscode.workspace.onDidChangeConfiguration((_) => {
            this._onDidChangeCodeLenses.fire();
        });
    }

    public provideCodeLenses(document: vscode.TextDocument, token: vscode.CancellationToken): vscode.CodeLens[] | Thenable<vscode.CodeLens[]> {
        const codeLenses: vscode.CodeLens[] = [];
        const regex = new RegExp(/(class|function|def)\s+([a-zA-Z0-9_]+)/g);
        const text = document.getText();
        let matches;

        while ((matches = regex.exec(text)) !== null) {
            const line = document.lineAt(document.positionAt(matches.index).line);
            const indexOf = line.text.indexOf(matches[0]);
            const position = new vscode.Position(line.lineNumber, indexOf);
            const range = document.getWordRangeAtPosition(position, new RegExp(matches[0]));

            if (range) {
                const codeLens = new vscode.CodeLens(range, {
                    title: "$(rocket) Ask Diffy",
                    command: "diffy.askQuestionContext",
                    arguments: [document.uri, range, matches[2]] // Send uri, range, and symbol name
                });
                codeLenses.push(codeLens);
            }
        }
        return codeLenses;
    }
}
