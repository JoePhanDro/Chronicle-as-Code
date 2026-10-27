---
target: event page
total_score: 23
max_score: 40
na_heuristics: 
p0_count: 0
p1_count: 2
target_identity: "file:/Users/philipprose/Documents/dev/Chronicle-as-Code/chronicle/templates/event.html"
target_fingerprint: "sha256:17d4c14a4233057ba69e2f3b23b1a14c60e81977935d246a92938cb30f23706d"
target_path: /Users/philipprose/Documents/dev/Chronicle-as-Code/chronicle/templates/event.html
timestamp: 2026-10-26T21-37-05Z
slug: chronicle-templates-event-html
---
Method: dual-agent (A: design review · B: detector + browser)

Score 23/40. Heuristics: 1=2, 2=3, 3=2, 4=3, 5=2, 6=2, 7=2, 8=2, 9=3, 10=2.
Design specificity: competent but interchangeable. Read view lacks phase colour, status, causality (triggered_by).

Priority issues
- [P1] Read mode lacks phase, status and causality (layout/colorize)
- [P1] Edit form dominates reading: form is ~70% of scroll length (distill)
- [P2] Löschen is first tab stop in main, native confirm only (harden)
- [P2] Markdown H1 (31px) outranks page h1 (20px) (typeset)
- [P3] Evidence layout and empty state, English "Evidence" (clarify)

Evidence (B): detector 1 advisory each (false positives), overused-font Inter. Contrast 0 failures except mermaid edge label 4.43. Focus visible on 33/33. Console error: pattern attribute on key input invalid with /v flag (_event_fields.html:22). Touch targets 21-22 below 44px at 375px, checkboxes 13px.
