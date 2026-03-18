# LEARNINGS

## [LRN-20260319-001] correction

**Logged**: 2026-03-18T22:02:00Z
**Priority**: medium
**Status**: pending
**Area**: docs

### Summary
A directory existence check for the lvyou-sms repo produced a false negative because the inspection method was too narrow / inconsistent with the actual repo structure.

### Details
The user corrected the claim that certain directories were missing. Future checks should verify paths directly from the repo root and avoid relying on truncated listings or overly narrow scans before stating something is absent.

### Suggested Action
When validating repository structure, use direct path existence tests and only report a path missing after explicit verification.

### Metadata
- Source: user_feedback
- Related Files: /root/.openclaw/workspace/lvyou-sms/README.md
- Tags: correction, repo-structure, verification

---
