# Putting this on GitHub (one time, ~5 minutes)

The point: colleagues download a .exe from the Releases page. Nobody needs
Python, and the build keeps working after whoever set it up has moved on.

1. Create an empty repository on GitHub — private is fine.
   Do **not** let it add a README or .gitignore; this folder already has them.

2. In this folder:

   ```bash
   git init
   git add .
   git commit -m "Core Photo Tool"
   git branch -M main
   git remote add origin https://github.com/<org-or-user>/<repo>.git
   git push -u origin main
   ```

3. Watch the **Actions** tab. The "Build Windows app" workflow runs on Windows
   and produces `CorePhotoTool.exe`. First run takes ~4 minutes.

4. Cut a version so people have a stable download link:

   ```bash
   git tag v1.0.0
   git push --tags
   ```

   The .exe, README and PHOTO_SOP now appear on the **Releases** page. Send
   colleagues that link.

That is all. Any future change pushed to `main` rebuilds automatically; tag it
when you want a new release.

Note: GitHub Actions is free for public repos and has a free monthly allowance
for private ones. This build uses a couple of minutes per run.
