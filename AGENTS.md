# Agent Notes

- After every code change, run `pytest` before handing work back. If the local environment cannot run pytest because dependencies are missing or the interpreter is unavailable, report that explicitly with the command attempted and the failure.
- After every code change and verification, commit the validated changes to git and push them to GitHub. If pushing fails because of network, authentication, or sandbox restrictions, report the exact command attempted and the failure.
- After every code refactor or addition, check for obsolete code left behind and for inconsistencies with existing project or file-level instructions. If any are found, notify the user and recommend a resolution that preserves instruction-level consistency and keeps the codebase concise; do not silently expand the task scope to resolve them.
