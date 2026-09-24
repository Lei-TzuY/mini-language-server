# Nova import-graph function visibility

The Nova imported-symbol namespace gives bounded semantic meaning to existing top-level relative file imports without introducing package/module search paths.

## Visibility contract

Function call lookup has two compatibility modes plus explicit declaration and module export controls:

- a file with no explicit imports keeps the historical workspace-global function lookup for ordinary functions, while foreign `private fn` declarations are excluded;
- once a file declares at least one supported relative import, same-file function declarations take precedence by name, and otherwise each directly imported module contributes its own bounded export view;
- a selective `import { name, source as local, ... } from ./path.nova;` narrows that one import edge after the target module's private/export rules have been applied; an aliased entry looks up the canonical source name but installs the chosen binding name in the importing module, while `import {} from ./path.nova;` imports nothing and the existing bare relative-import form remains unchanged;
- `private fn name(...) { ... }` remains fully visible inside its declaring module but is omitted from every foreign export view;
- a top-level `export { name, ... };` list, when present, filters that module's outward function view to the named functions after local-first/import visibility has been computed; `export {};` explicitly exports nothing;
- an export entry may select one uniquely visible imported binding, including an alias, so aliases can propagate only through the same existing module outward-view/export rules while their canonical declaration identity remains unchanged;
- that export view applies the same local-first rule recursively before propagating transitively, so any local declaration shadows same-named declarations from its own imports. A private local therefore blocks a deeper same-named declaration from being re-exported accidentally.

Import traversal is transitive across actual supported import edges, canonical declarations are deduplicated by exact snapshot/symbol identity, and cycles terminate without duplicating candidates. A file reached through multiple diamond paths contributes each declaration once. Unresolved or unsupported imports contribute no declarations and continue to report the existing `nova.unresolved-import` diagnostic. Selective import entries are validated against the target module's exact outward export view: unresolved/ambiguous diagnostics are keyed to the canonical source selector, while `nova.duplicate-import-name` rejects duplicate importer-local binding names. Multiple distinct aliases may still bind the same uniquely resolved canonical declaration. Explicit export entries report `nova.unresolved-export`, `nova.ambiguous-export`, `nova.private-export`, or `nova.duplicate-export` when their exact captured module namespace cannot justify the requested outward name.

The local-first rule applies only after a file has opted into explicit imports. Ordinary unmodified `fn` declarations therefore preserve historical visibility, while the explicit private modifier is honored for both import-graph callers and legacy no-import foreign callers.

## Shared tooling surfaces

The visibility resolver is shared by the executable function-call surfaces in this phase rather than reimplemented per request:

- open and detached workspace call diagnostics, including argument-count/type and downstream detached product diagnostics that consume the scoped function map;
- definition and negotiated DefinitionLink target enrichment;
- references;
- hover and signature help;
- workspace completion and inferred-return completion detail;
- call-hierarchy prepare/incoming/outgoing resolution;
- prepare-rename and exact workspace function rename;
- reference CodeLens counts and the matching execute-command locations;
- function-reference semantic tokens, including range/full/delta publication through the existing exact-workspace token gate;
- parameter inlay hints and their existing workspace refresh lifecycle;
- argument-count and argument-type call-site quick-fix revalidation.

References, incoming calls, and CodeLens locations are attributed by canonical declaration identity after each caller's own exact import visibility is resolved, so alias-spelled calls contribute to the same symbol while unrelated same-spelled calls do not. Definition/hover/completion/signature/semantic-token and call-site repair surfaces resolve the alias spelling through the same central map. Project monikers keep the canonical declaration name. Canonical function rename updates the declaration, canonical-spelled calls, exact selective-import source selectors, and exact same-named export selectors while preserving alias spellings. An alias binding whose importer has an explicit export list that omits that alias is independently renameable from either the alias token or an alias-spelled call: the edit changes only the exact alias token plus same-importer alias-spelled calls, retains canonical provider source/declaration identity, rejects importer binding collisions, and reuses the exact workspace commit plus closed-snapshot revalidation boundary. Aliases in an explicit outward export list now support one bounded API-rename graph: the origin alias token, same-module alias-spelled calls, exact export selector, downstream explicit selective-import source selectors, and downstream unaliased calls/exports are renamed only while every propagating edge is backed by the same exact canonical declaration identity. A downstream aliased import updates only its source selector and keeps its importer-local binding stable. Bare imports, implicit outward exports from modules without an explicit export list, ambiguous/duplicate bindings, or unsupported graph edges still fail closed rather than guessing API propagation.

All request surfaces retain their existing exact workspace snapshot commit gates. Import visibility is computed from the same captured workspace snapshot set used to publish the result; it is not re-resolved against newer workspace state during publication.

## Deliberate nonclaims

This phase does not claim a general Nova module system. It does not add:

- package or module search paths;
- wildcard imports, bare-import alias API propagation, or alias graph rename through implicit exports/package namespaces;
- friend/package visibility, wildcard export forms, or declaration-level visibility beyond bounded `private fn` plus explicit function export lists;
- non-function imported namespaces;
- cross-authority or remote-provider module resolution;
- a stable external package identity.

Transitive visibility is intentionally limited to function declarations reachable through existing bare or selective relative-file import edges plus the bounded private-function and explicit export-list rules above; it does not imply package identity, wildcard namespaces, arbitrary exported alias API propagation, or non-function exports. Importer-private aliases remain independently renameable, and outward aliases are renameable only across the bounded explicit selective-import / explicit-export graph described above. Specialized later product layers that are not part of the shared call-resolution surfaces above remain future namespace-parity work and must not infer broader module semantics from this bounded contract.
