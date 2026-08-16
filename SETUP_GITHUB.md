# Putting this on GitHub

> **If you upload by dragging files into the browser, the `.github` folder is
> silently skipped.** Browsers exclude dot-folders from folder drops. Without it
> there is no workflow, so no automatic Windows build ever runs — and no `.exe`.
> Use git (below), or create that one file by hand afterwards.

## The easy way: push_to_github.bat

Edit `REPO_URL` at the top of `push_to_github.bat`, save, double-click. It
initialises the repo, builds on top of anything already there, pushes `main`,
and tags `v1.0.0` so you get a Releases page. Needs git installed
(https://git-scm.com/download/win).

## By hand

```bash
git init
git add .
git commit -m "Core Photo Tool"
git branch -M main
git remote add origin https://github.com/<user>/<repo>.git
git fetch origin main && git reset --soft FETCH_HEAD    # only if the repo already has commits
git add -A && git commit -m "Core Photo Tool"
git push -u origin main
git tag v1.0.0 && git push --tags
```

## If you already uploaded through the browser and the build never ran

You are missing exactly one file. On the repo page:

1. **Add file → Create new file**
2. In the filename box type `.github/workflows/build-windows.yml` — typing the
   slashes creates the folders.
3. Paste the contents of `.github/workflows/build-windows.yml` from this zip.
4. Commit.

The build starts immediately. Watch the **Actions** tab; ~4 minutes.

## Getting a Releases page

Until you tag a version, the `.exe` only exists as an Actions artifact, which is
awkward to find. Tag it:

```bash
git tag v1.0.0
git push --tags
```

Actions is free for public repos; this build uses a couple of minutes per run.
