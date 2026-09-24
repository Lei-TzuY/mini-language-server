# Nova direct-import function visibility

The first Nova imported-symbol namespace slice gives bounded semantic meaning to existing top-level relative file imports without introducing package/module search paths.

## Visibility contract

Function call lookup has two compatibility modes:

- a file with no explicit imports keeps the historical workspace-global function lookup;
- once a file declares at least one supported relative import, same-file function declarations take precedence by name, and otherwise only functions declared by directly imported exact workspace snapshots are visible.

Direct imports are not transitive. Importing a file does not re-export the functions visible to that file. Duplicate import paths to the same canonical workspace identity do not duplicate candidates. Unresolved or unsupported imports contribute no declarations and continue to report the existing `nova.unresolved-import` diagnostic.

The local-first rule applies only after a file has opted into explicit imports. This preserves historical no-import behavior while allowing an importer to distinguish two same-named functions that live in different workspace files.

## Shared tooling surfaces

The visibility resolver is shared by the executable function-call surfaces in this phase rather than reimplemented per request:

- open and detached workspace call diagnostics, including argument-count/type and downstream detached product diagnostics that consume the scoped function map;
- definition and negotiated DefinitionLink target enrichment;
- references;
- hover and signature help;
- workspace completion and inferred-return completion detail;
- call-hierarchy prepare/incoming/outgoing resolution;
- prepare-rename and exact workspace function rename;
- reference CodeLens counts and the matching execute-command locations.

References, rename edits, incoming calls, and CodeLens locations are included only when that caller's own exact import visibility resolves uniquely to the same declaration object. A same-named call in another module therefore cannot be attributed to the selected declaration merely because the spelling matches.

All request surfaces retain their existing exact workspace snapshot commit gates. Import visibility is computed from the same captured workspace snapshot set used to publish the result; it is not re-resolved against newer workspace state during publication.

## Deliberate nonclaims

This phase does not claim a general Nova module system. It does not add:

- package or module search paths;
- import aliases or selective/wildcard imports;
- transitive imports or re-export semantics;
- visibility modifiers or exported/private declarations;
- non-function imported namespaces;
- cross-authority or remote-provider module resolution;
- a stable external package identity.

Specialized later product layers that are not part of the shared call-resolution surfaces above remain future namespace-parity work and must not infer broader module semantics from this bounded contract.
