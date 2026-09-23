# Analysis helper: method inventory of YTDLPDownloaderGUI grouped by concern.
# Run from the repo root:  python3 temp/analyze_app_split.py

import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ytdl", "app.py")).read()
tree = ast.parse(src)

for node in tree.body:
    if isinstance(node, ast.ClassDef) and node.name == "YTDLPDownloaderGUI":
        methods = [(n.name, n.end_lineno - n.lineno + 1) for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        print(f"class YTDLPDownloaderGUI: {node.end_lineno - node.lineno + 1} lines, {len(methods)} methods")
        groups = {
            "UI build": ("init_ui", "setup_menu_bar", "create_menu_bar", "apply_styles", "create_option_group", "setDockTileCheck", "setDockProgressOverlay", "clearDockProgress", "handle_indeterminate_state"),
            "info fetch": ("fetch_video_info", "get_video_info", "check_sponsorblock", "_emit_description_summary", "_get_description_summary", "_dump_ytdlp_error_output"),
            "download": ("start_download", "build_command", "run_download", "get_filename_template", "get_full_path", "get_output_dir", "get_file_metadata", "open_thumbnail_dialog"),
            "subtitles": ("_subtitle_lang_patterns", "_find_downloaded_subtitles", "_normalize_subtitle_names", "_subtitle_needs_resync", "_resync_subtitle_for_language", "resync_subtitles", "_is_subtitle_path", "get_selected_subtitle_codes", "_update_subtitle_checkboxes", "_auto_select_subtitles"),
            "subtitle resync internals": tuple(n.name for n in node.body if isinstance(n, ast.FunctionDef) and n.name.startswith(("_merge_", "_format_subtitles", "_build_time_map", "_adjust_timestamp", "_calculate_time", "_strip_nonspoken", "_last_sentence", "_tokenize"))),
            "preferences": tuple(n.name for n in node.body if isinstance(n, ast.FunctionDef) and "preferences" in n.name),
            "dock (macOS)": tuple(n.name for n in node.body if isinstance(n, ast.FunctionDef) and "dock" in n.name.lower()),
        }
        sizes = dict(methods)
        assigned = set()
        for gname, names in groups.items():
            present = [n for n in names if n in sizes]
            total = sum(sizes[n] for n in present)
            assigned.update(present)
            print(f"  {gname}: ~{total} lines ({len(present)} methods)")
        rest = [(n, s) for n, s in methods if n not in assigned]
        print(f"  other ({len(rest)} methods): ~{sum(s for _, s in rest)} lines")
        for n, s in sorted(rest, key=lambda x: -x[1])[:20]:
            print(f"      {n}: {s}")
        break
