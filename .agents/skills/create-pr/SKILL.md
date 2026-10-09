---
name: create-pr
description: Create a GitHub pull request for current changes in this project when the user asks to create or open a PR. Branch, rebase onto main, commit, push, write the PR description, and share its link.
---

# Create PR

This skill applies to the repository containing this skill. Run in its active
checkout or worktree. A request to create a PR authorizes the workflow below;
do not ask for redundant confirmation. Respect tool permission requirements.
Do not merge the PR or push directly to main.

## Inspect

- Read applicable AGENTS.md instructions. Inspect status, staged and unstaged
  diffs, untracked files, existing commits, worktrees, and remotes.
- Resolve the GitHub repository and remote from Git configuration. Use `gh` when
  available and check authentication without exposing credentials.
- Use `main` as the base unless the user specifies another branch. Fetch the
  remote and verify the base exists. If it is missing, ask which base to use.
- Identify intended changes from the task and diff, including relevant existing
  commits. Preserve unrelated edits and staged state. Ask only when scope is
  materially ambiguous. Exclude secrets, local environment files, and unrelated
  generated output.

## Branch, rebase, and commit

1. Create a unique descriptive `codex/<change-summary>` branch at current HEAD,
   including when HEAD is detached. Honor an explicitly requested branch name.
   Never reset an existing branch or alter another worktree's checkout.
2. Rebase the new branch onto the fetched remote main. If the working tree is
   dirty, first save tracked and untracked edits in a dedicated stash. Record its
   exact object ID and original status. Do not stash ignored files or use clean
   or hard reset. Restore the saved edits and staged state after the rebase with
   stash apply. Keep the stash until every saved change is verified restored;
   then remove only that identified stash entry. Preserve unrelated edits locally.
3. Resolve conflicts only when task context makes the intended result clear.
   Never blindly choose ours/theirs. If a decision is needed, stop and report the
   conflict while preserving work and recovery information.
4. Run repository-required checks appropriate to the changes. Follow TDD for
   executable fixes made during this workflow. Do not claim pre-existing changes
   were developed test-first. Documentation-only changes need content checks,
   not artificial tests. Report failures or blocked checks honestly.
5. Review the final changes, stage only intended paths or hunks, and commit with
   a concise descriptive message. Do not create an empty commit if all intended
   changes are already committed. If no difference from the base remains, report
   that there is nothing to open a PR for.

## Push and create

1. Inspect the complete `remote/main...HEAD` diff and commit list, substituting
   the resolved remote and base. Verify that the PR contains only intended work.
2. Push the new branch with an upstream using a normal push. Never force push.
   On a branch-name collision, select a fresh name. After an uncertain push or
   create response, inspect remote state before retrying to prevent duplicates.
3. Follow the repository's PR template if present. Write a title and description
   based on the complete final diff. Explain the problem and resulting behavior,
   summarize the changes, and list actual validation results and limitations.
   Do not invent passing checks, issue links, or test-first evidence. Use a draft
   if required checks fail or remain blocked, or if the user requests one.
4. Check for an existing PR for the exact head and base before creating one;
   reuse it if found. For `gh pr create`, specify repository, base, and head
   explicitly. Write multiline Markdown to a temporary file and use `--body-file`
   rather than interpolating descriptions into shell code.
5. Verify the PR URL and head/base. Attach the PR to this task using the Codex app
   attachment tool when available.

## Handoff

Return a clickable PR link, a short recap of the requested work and implementation,
and validation results. Mention pending checks, unrelated local edits, or retained
recovery stashes. If blocked, report completed steps and the exact blocker instead
of claiming the PR exists. Do not perform a real push or open a PR merely to test
this skill during its creation.
