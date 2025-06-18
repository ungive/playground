# macos-release-tracker

In your repository where you want to track releases:

```
mkdir -p .github/workflows
cd .github/workflows
git submodule add macos-release-tracker https://github.com/ungive/github-macos-release-tracker.git
cp macos-release-tracker/example-workflow.yml macos-release-tracker.yml
```

Adjust the paths in the workflow, if necessary.

Commit and push.
