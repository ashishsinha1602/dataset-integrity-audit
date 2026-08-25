---
title: Dataset Integrity Auditor
emoji: 🔍
colorFrom: blue
colorTo: gray
sdk: gradio
sdk_version: 6.22.0
app_file: app.py
pinned: false
license: mit
---

# Dataset Integrity Auditor

How much of a benchmark is actually redundant? Paste a Hugging Face dataset id.

Separates records that are **identical across every evaluated field** from
records that merely **share a question stem**. Naive similarity lumps them
together and overstates duplication — by 9x on Spider's training split.

Six benchmarks audited so far, all clean. Code and write-up:
https://github.com/ashishsinha1602/bird-critic-audit
