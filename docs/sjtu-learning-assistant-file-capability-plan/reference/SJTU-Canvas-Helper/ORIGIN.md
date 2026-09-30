# Origin and Scope

This directory contains a curated source-reference subset copied from:

- Project: SJTU Canvas Helper
- Source: https://github.com/Okabe-Rintarou-0/SJTU-Canvas-Helper
- Revision: `8f57a2b1c50998b4b2b7e53168824625d7e81b78`
- License: MIT License
- Copyright: Copyright (c) 2025 Zihong Lin

The original `LICENSE` is included unchanged in this directory. Files retain their original relative paths to make provenance review straightforward.

## Included scope

- Typed Tauri event lifecycle hooks
- File preview modal and renderer registry
- Renderer shell and PDF renderer
- Download-task table and retry/progress UI
- File-level AI conversation UI
- Rust download/client implementation for algorithm reference
- Rust AI client and file parser implementations
- Rust error model
- Original package manifest and README

## Intended use

This is an audit and implementation-reference package, not a drop-in dependency. The target application uses Python + pywebview + React rather than Rust + Tauri + MUI. Any copied or adapted portion must keep the MIT attribution and be recorded in the target project's README/THIRD_PARTY_NOTICES as described in `../../OPEN_SOURCE_ATTRIBUTION.md`.
