---
name: csp
description: >-
  Commit already-staged changes and push to remote. Use when the user says csp,
  commit staged and push, commit staged, or wants to ship staged work without
  restaging or reviewing unstaged files.
---

# Commit Staged and Push (csp)

Commit **only** what is already staged, then push. Do not `git add` unless the
user explicitly asks to stage files first.

## Preconditions

If nothing is staged, stop and tell the user to stage files (e.g.
`git add <paths>`). Do not stage everything automatically.

## Step 1: Inspect staged changes

Run in parallel from repo root:

```bash
git status
git diff --staged
git log -5 --oneline
```

Review **staged** changes only. Ignore unstaged and untracked unless the user
asks to include them.

## Step 2: Draft commit message

- 1–2 sentences, focus on **why** not just what
- Match recent commit message style from `git log`
- Do not commit secrets (`.env`, credentials, tokens)

## Step 3: Commit staged changes

Use a HEREDOC for the message. `git commit` commits staged files only — do
not run `git add` here.

```bash
git commit -m "$(cat <<'EOF'
Your commit message here.

EOF
)"
```

### If commit fails (hook modified files)

- Do **not** amend unless all amend rules apply (you created HEAD, not pushed)
- Fix issues and create a **new** commit

### Amend (only when allowed)

Amend only if **all** are true:

1. User explicitly requested amend, **or** hook auto-modified files after a
   successful commit you created
2. HEAD commit was created by you in this session
3. Commit has **not** been pushed

## Step 4: Push

```bash
git push -u origin HEAD
```

Use `-u` when the branch has no upstream. Do not force-push `main`/`master`.

## Step 5: Verify

```bash
git status
```

Confirm working tree state and that the branch is up to date with remote.

## Do not

- Update git config
- Run destructive git commands (force push, hard reset) unless explicitly asked
- Skip hooks (`--no-verify`, `--no-gpg-sign`) unless explicitly asked
- Stage files unless the user asks
- Commit unstaged or untracked changes
- Push when the user only asked to commit (this skill includes push by default)

## Quick reference

| Step | Command |
|------|---------|
| Staged diff | `git diff --staged` |
| Commit staged | `git commit -m "$(cat <<'EOF'…EOF')"` |
| Push | `git push -u origin HEAD` |
| Verify | `git status` |
