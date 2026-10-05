---
name: k2b-capcut
description: Edit or inspect a local CapCut desktop project on Keith's Mac with a verified keyboard map, deliberate timeline focus, and post-edit checks. Use for hands-on CapCut project work or shortcut-driven editing.
---

# CapCut desktop editing

Use the CapCut app UI for interactive editing. On the SJM Mac, CapCut 9.5.0 was inspected on 2026-10-04 with keymap **Shortcut 1**. Treat this as a tested snapshot, not a permanent default: check the installed version and the editor's keyboard icon/keymap before relying on a binding. Global CapCut Settings > Edit showed a 30 fps timebase, `HH:MM:SS+frame` timecode, and **Step forward or back many = 10 frames**. The keyboard map is in the editor's top-right keyboard dialog, separate from global Settings.

## Project and edit protocol

1. Activate CapCut and confirm its editor window is present. Confirm the project title in the editor and its path in Details. For an editing session, use a duplicate: create one from the project's card on the CapCut home screen via **… > Duplicate**, then open the copy and verify its title before editing. An existing verified working copy can be reused. Keep the source project untouched unless Keith explicitly directs edits to it.
2. Before **each** shortcut, confirm CapCut is the active app and inspect the live editor: which panel or field has keyboard focus, which clip is selected (or explicitly none), and the exact playhead timecode. If a search box, shortcut dialog, or EditPilot text field has focus, return focus to the editor/timeline and inspect again. Never infer focus from the last click alone. If computer use reports an app switch, reacquire CapCut and repeat these checks before retrying.
3. Apply one shortcut, then inspect the timecode, selected clip, timeline boundaries or markers, and relevant inspector state. If the visible result is ambiguous, stop and investigate before another edit. Do not rely on a shortcut list as proof that an edit happened.
4. Report the source project, working copy, operations verified, and final state. Preserve the duplicate as the test artifact unless Keith asks to remove it.

CapCut autosaves; treat the duplicate as the durable protection for the source. Do not count undo history as a backup across app restarts. If an edit is wrong, recheck app focus, selection, and playhead before one undo, then verify the restored state. If undo fails or the state remains unclear, stop editing and preserve the open session. Record the affected project name/path, last operation, and visible state. When only the duplicate is damaged and the source is verified intact, retain the damaged copy and start a fresh duplicate from the source. If the source was edited by mistake, report the incident to Keith; no restoration mechanism beyond the tested in-session undo is verified by this skill. CapCut cloud/history restoration has not been inspected. Do not close/reopen the app, overwrite a draft, or issue repeated undo blindly.

The local project directory observed on SJM was `/Users/keithcheung/Movies/CapCut/User Data/Projects/com.lveditor.draft`. Confirm the current path in CapCut Settings before any read-only file diagnosis; this path does not authorize direct draft writes or an untested restore.

## Shortcuts tested on this Mac

The following were tested in `1004 (1)-copy` on a selected **audio** clip, in the order shown. The editor window, selected clip, and playhead were checked immediately before each keypress; the result was inspected afterward.

| Shortcut | Action and observed result |
|---|---|
| Right Arrow | Advanced `00:01:37:27` to `00:01:37:28`, one frame. |
| Command+B | Split the selected audio clip at `00:01:37:28`; two adjacent clip pieces appeared and the right piece became selected. |
| Command+Z | Undid that split; one continuous audio clip returned and the playhead stayed at `00:01:37:28`. |
| Shift+Right Arrow | Advanced `00:01:37:28` to `00:01:38:08`, ten frames at 30 fps. |

The keymap also *displayed* Left Arrow as previous frame, Shift+Left Arrow as the backward many-frame step, Space as play/pause, Command+Shift+B as Split all, A as Select mode, B as Split mode, Q/W as Delete left/right, M as Add marker, Command+Plus/Minus as timeline zoom, and Shift+Z as timeline zoom to fit. Those were not exercised in this session. Verify them against the current keymap and an actual result before treating them as tested workflows. `Command+Shift+Z` was labeled **Reset** in the Basic tab; never send it as a presumed Redo. No redo binding was tested; re-perform the intended edit from the verified restored state when needed.

The tested split was on audio. Recheck selection and outcome for video, linked clips, compound clips, and other media types rather than assuming identical behavior.

## Draft-format references

Community projects can help explain CapCut drafts, but their schemas are version-sensitive. [capcut-mcp](https://github.com/JmsLdrn/capcut-mcp) warns about concurrent CapCut autosave and draft-file writes; [chukcut's draft notes](https://github.com/chukfinley/chukcut/blob/master/docs/research/draft-format.md) document a different platform/version. Use them as reference material, not as proof that direct JSON edits are safe for this Mac's open project. Prefer verified UI editing for this workflow.
