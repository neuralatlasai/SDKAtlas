# Repository controls and remaining adoption decisions

This directory supplies source, tests, package metadata, CI definitions, and
review guidance. It does not establish a GitHub repository or configure hosted
repository permissions. Satisfying source checks does not imply that all process
controls in `standards.md` have been enforced.

Before external distribution, the repository owner must make these decisions:

1. Select a license, obtain the necessary rights, add the actual `LICENSE` text,
   and declare the chosen license in `pyproject.toml`. No license grant or
   copyright ownership is invented by this scaffold.
2. Establish a hosted repository and default branch. Put this project's
   `.github/` at that repository's root; GitHub does not discover workflow files
   nested under an arbitrary parent project directory. If this remains part of a
   monorepo, move the workflows to the monorepo root and adjust working paths.
3. Require pull requests, the Quality and Dependency security checks, and at
   least one qualified reviewer on default/release branches. Dismiss stale
   approvals and require review of the latest revision where supported.
4. Disable force pushes and branch deletion for protected default/release
   branches. Restrict release-tag creation to authorized maintainers.
5. Add `.github/CODEOWNERS` only after real qualified reviewer identities are
   known. Require owner approval for higher-risk modules and workflows when
   supported. A placeholder identity would falsely imply enforced ownership.
6. Enable secret scanning, dependency alerts, private vulnerability reporting,
   and least-privilege repository access where the hosting plan permits them.
7. Configure a protected release environment and trusted publishing identity
   before adding any package-index publication job. Current workflows only
   validate and retain build artifacts; they do not publish a package.

Capture the repository settings and responsible reviewers in the change record.
These controls require repository-administrator access and cannot be completed
by editing files in a non-Git local workspace.

The standards' hardware, lock-free concurrency, zero-copy, and nanosecond
instrumentation guidance applies when those mechanisms are relevant. A
single-process Python AST inventory does not benefit from synthetic atomics,
arena allocators, or kernel networking interfaces. Python's own resource and
runtime contracts govern these implementation choices.
