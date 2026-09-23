# WorkspaceEdit change annotations

The language-independent WorkspaceEdit renderer now negotiates LSP change annotations for existing rename and Nova quick-fix workflows.

## Negotiation

The server records `workspace.workspaceEdit.changeAnnotationSupport` during `initialize`. Change annotations are emitted only when the client also supports `workspace.workspaceEdit.documentChanges = true`, because annotated text edits belong to the versioned `TextDocumentEdit` representation. Clients using legacy `WorkspaceEdit.changes` keep ordinary `TextEdit[]` payloads unchanged.

An empty `changeAnnotationSupport` object is sufficient to advertise support. The optional `groupsOnLabel` preference does not change server correctness and requires no separate payload shape.

## Semantic labels

The shared renderer accepts one optional semantic label from its consumer:

- rename uses `Rename 'old' to 'new'`;
- Nova code actions use the existing action title, such as `Create function 'missing'`.

When annotations are active and at least one edit exists, the renderer emits a deterministic annotation identifier (`edit:1`) in every edit and a matching `WorkspaceEdit.changeAnnotations` entry. Annotation identifiers are scoped to one WorkspaceEdit, so deterministic reuse across separate responses is valid.

The renderer does not invent a generic label. Consumers that do not provide a semantic label receive the ordinary versioned edit shape even when the client supports annotations.

## Exactness

Annotations do not change snapshot ownership. Rename edits remain bound to the exact semantic/workspace snapshots and captured document versions that produced them. Eager Nova quick fixes are annotated while their exact diagnostic-owned edit is assembled. Lazy code-action resolve stores that already-rendered annotated edit inside the existing diagnostic/workspace-bound resolve record and restores it only if those exact snapshots remain current.

No resource operations are introduced by this milestone. File create/rename/delete operations remain future work until a concrete editor workflow requires them.
