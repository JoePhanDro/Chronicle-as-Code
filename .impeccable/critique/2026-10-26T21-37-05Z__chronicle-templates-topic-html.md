---
target: topic page
total_score: 22
max_score: 40
na_heuristics: 
p0_count: 0
p1_count: 2
target_identity: "file:/Users/philipprose/Documents/dev/Chronicle-as-Code/chronicle/templates/topic.html"
target_fingerprint: "sha256:d8c674c1171bf75a2be7c8c9d68d7b2d9ea5b1e602156c7010f8574762446347"
target_path: /Users/philipprose/Documents/dev/Chronicle-as-Code/chronicle/templates/topic.html
timestamp: 2026-10-26T21-37-05Z
slug: chronicle-templates-topic-html
---
Method: dual-agent (A: design review · B: detector + browser)

Score 22/40. Heuristics: 1=2, 2=3, 3=2, 4=2, 5=2, 6=2, 7=2, 8=2, 9=3, 10=2.
Design specificity: generic CRUD detail page around a specific dashboard. No status or recency, which /topics shows.

Priority issues
- [P1] Dashboard iframe double-scrolls (80vh fixed, content 771-1045px) and duplicates title/summary (layout)
- [P1] Hierarchy: status, story and controls in the wrong order; Löschen at title level (clarify/layout)
- [P2] Event list duplicates the dashboard, low information rows (distill)
- [P2] Edit form, link select (all events) and delete always open; unlink without confirm (adapt/harden)
- [P3] Empty topic and long-title states (onboard)

Evidence (B): contrast 0 failures outer and iframe; iframe theme sync works; unlink has no confirm; 375px targets below 44px: links 16px, buttons 34-38px.
