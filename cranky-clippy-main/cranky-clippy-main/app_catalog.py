"""Static application and website catalog used by desktop-state detection.

This module defines app categories, app/site metadata, aliases, package/profile
locations, and identity matching. It does not query the desktop, filesystem, or
network; get_desktop_state.py owns those runtime observations.

Everything Windows-specific lives under an explicit IS_WINDOWS check, so the
shared (Linux) tables above are never altered to accommodate Windows.
"""

import functools
import os
import re
import sys

# App-specific metadata — the extra fields Jev would need for each app to
# judge "distraction or not". Apps whose name is self-explanatory (Discord,
# Slack, etc.) only need generic window data; apps where content varies get
# their own metadata fields.
#
# Structure: APP_METADATA is a dict of app "classes". Each class maps app
# names to a list of metadata fields to collect. Browsers get their own class
# and also have their own sub-class, WEBAPP_METADATA, for sites that are
# opened inside a browser (YouTube, Notion, Reddit, ...). Those sites are not
# apps; they are extra metadata layered on top of the browser's own fields,
# picked out by matching the site_domain.

APP_METADATA = {
    # CLASS: browser. A tab can be anything, so we need to see inside it.
    # Supported: Chrome, Chromium, Firefox, Edge, Safari, Brave, Opera, Arc,
    # Vivaldi. These fields are collected for every browser window, and then
    # WEBAPP_METADATA below adds site-specific fields on top.
    "browser": {
        "chrome": ["site_url", "site_domain", "tab_title", "tab_focus_seconds", "history_domains"],
        "chromium": ["site_url", "site_domain", "tab_title", "tab_focus_seconds", "history_domains"],
        "firefox": ["site_url", "site_domain", "tab_title", "tab_focus_seconds", "history_domains"],
        "edge": ["site_url", "site_domain", "tab_title", "tab_focus_seconds", "history_domains"],
        "safari": ["site_url", "site_domain", "tab_title", "tab_focus_seconds", "history_domains"],
        "brave": ["site_url", "site_domain", "tab_title", "tab_focus_seconds", "history_domains"],
        "opera": ["site_url", "site_domain", "tab_title", "tab_focus_seconds", "history_domains"],
        "arc": ["site_url", "site_domain", "tab_title", "tab_focus_seconds", "history_domains"],
        "vivaldi": ["site_url", "site_domain", "tab_title", "tab_focus_seconds", "history_domains"],
        # Windows-flavoured Chromium browsers (also packaged for Linux). Same
        # fields as the browsers above; their active tab is read the same way.
        "yandex": ["site_url", "site_domain", "tab_title", "tab_focus_seconds", "history_domains"],
        "ungoogled_chromium": ["site_url", "site_domain", "tab_title", "tab_focus_seconds", "history_domains"],
        "iridium": ["site_url", "site_domain", "tab_title", "tab_focus_seconds", "history_domains"],
        "comodo_dragon": ["site_url", "site_domain", "tab_title", "tab_focus_seconds", "history_domains"],
        "epic_privacy_browser": ["site_url", "site_domain", "tab_title", "tab_focus_seconds", "history_domains"],
        "tor_browser": ["site_url", "site_domain", "tab_title", "tab_focus_seconds", "history_domains"],
        "cent_browser": ["site_url", "site_domain", "tab_title", "tab_focus_seconds", "history_domains"],
        "maxthon": ["site_url", "site_domain", "tab_title", "tab_focus_seconds", "history_domains"],
        "coc_coc": ["site_url", "site_domain", "tab_title", "tab_focus_seconds", "history_domains"],
        "avast_secure_browser": ["site_url", "site_domain", "tab_title", "tab_focus_seconds", "history_domains"],
        "ccleaner_browser": ["site_url", "site_domain", "tab_title", "tab_focus_seconds", "history_domains"],
        "kinza": ["site_url", "site_domain", "tab_title", "tab_focus_seconds", "history_domains"],
    },
    # CLASS: video player (native apps). What's playing and whether it plays.
    "video": {
        "vlc": ["media_title", "playback_state"],
        "mpv": ["media_title", "playback_state"],
        # Windows media players
        "mpc_hc": ["media_title", "playback_state"],
        "potplayer": ["media_title", "playback_state"],
        "kodi": ["media_title", "playback_state"],
        "windows_media_player": ["media_title", "playback_state"],
        "media_player": ["media_title", "playback_state"],
    },
    # CLASS: music player (native apps). Keeps "what is playing" apart from
    # video: a music app is a different kind of distraction from a movie.
    "media": {
        "spotify": ["media_title", "playback_state"],
        "foobar2000": ["media_title", "playback_state"],
        "musicbee": ["media_title", "playback_state"],
        "aimp": ["media_title", "playback_state"],
        "winamp": ["media_title", "playback_state"],
        "itunes": ["media_title", "playback_state"],
    },
    # CLASS: code editor. Which file/branch hints at whether it's the right project.
    "editor": {
        "code": ["workspace_name", "file_path", "git_branch", "active_file_focus_seconds", "language"],
        "jetbrains": ["project_name", "file_path", "git_branch", "tool_window_focused"],
        "sublime_text": ["project_name", "file_path"],
        "notepad++": ["file_path"],
        "vim": ["file_path", "buffer_name"],
        "neovim": ["file_path", "buffer_name"],
        "emacs": ["file_path", "buffer_name"],
        # KDE full IDE with its own window chrome
        "kate": ["file_path", "document_name"],
        "idle": ["file_path", "language"],
        "arduino": ["project_name", "file_path", "board_name"],
        # Windows editors and IDEs
        "notepad": ["file_path", "language"],
        "visual_studio": ["project_name", "file_path", "solution_name"],
        "pycharm": ["project_name", "file_path"],
        "intellij_idea": ["project_name", "file_path"],
        "webstorm": ["project_name", "file_path"],
        "rider": ["project_name", "file_path"],
        "goland": ["project_name", "file_path"],
        "phpstorm": ["project_name", "file_path"],
        "clion": ["project_name", "file_path"],
        "android_studio": ["project_name", "file_path"],
        "textpad": ["file_path"],
        "hxd": ["file_path"],
        "brackets": ["project_name", "file_path"],
        "dreamweaver": ["file_path"],
    },
    # AI coding environments and agents. Keep these distinct from general
    # conversational assistants and ordinary editors.
    "ai_coding": {
        "opencode": ["workspace_name", "window_context"],
        "antigravity": ["workspace_name", "window_context"],
        "zcode": ["workspace_name", "window_context"],
        "cursor": ["workspace_name", "file_path", "git_branch", "window_context"],
        "windsurf": ["workspace_name", "file_path", "git_branch", "window_context"],
        "zed": ["workspace_name", "file_path", "git_branch", "window_context"],
        "codex": ["workspace_name", "window_context"],
        "claude_code": ["workspace_name", "window_context"],
        "gemini_cli": ["workspace_name", "window_context"],
        "aider": ["workspace_name", "window_context"],
        "goose": ["workspace_name", "window_context"],
        "openhands": ["workspace_name", "window_context"],
        "cline": ["workspace_name", "window_context"],
        "roo_code": ["workspace_name", "window_context"],
        "continue": ["workspace_name", "window_context"],
        "github_copilot": ["workspace_name", "window_context"],
        "kiro": ["workspace_name", "window_context"],
        "devin": ["workspace_name", "window_context"],
        "amp": ["workspace_name", "window_context"],
        # Windows-first AI editors
        "trae": ["workspace_name", "file_path", "git_branch", "window_context"],
        "codebuddy": ["workspace_name", "file_path", "window_context"],
        "qoder": ["workspace_name", "file_path", "window_context"],
        "augment_code": ["workspace_name", "file_path", "window_context"],
    },
    # General-purpose conversational AI clients. This records their function
    # without pre-judging whether a particular conversation serves the goal.
    "ai_chat": {
        "chatgpt": ["conversation_title", "model_name"],
        "claude": ["conversation_title", "model_name"],
        "deepseek": ["conversation_title", "model_name"],
        "gemini": ["conversation_title", "model_name"],
        "copilot": ["conversation_title", "model_name"],
        "perplexity": ["conversation_title", "model_name"],
        "grok": ["conversation_title", "model_name"],
        "mistral_le_chat": ["conversation_title", "model_name"],
        "poe": ["conversation_title", "model_name"],
        "qwen_chat": ["conversation_title", "model_name"],
        "meta_ai": ["conversation_title", "model_name"],
        "kimi": ["conversation_title", "model_name"],
        "zai_chat": ["conversation_title", "model_name"],
        "chatbox": ["conversation_title", "model_name"],
        "lm_studio": ["conversation_title", "model_name"],
        "jan": ["conversation_title", "model_name"],
        "msty": ["conversation_title", "model_name"],
        # Windows AI clients and local-model front ends
        "cherry_studio": ["conversation_title", "model_name"],
        "anything_llm": ["conversation_title", "model_name"],
        "open_webui": ["conversation_title", "model_name"],
        "gpt4all": ["conversation_title", "model_name"],
        "llamacpp": ["conversation_title", "model_name"],
        "chatwise": ["conversation_title", "model_name"],
        "better_chatgpt": ["conversation_title", "model_name"],
        "monica": ["conversation_title", "model_name"],
        "quark": ["conversation_title", "model_name"],
        "doubao": ["conversation_title", "model_name"],
        "ernie_bot": ["conversation_title", "model_name"],
    },
    "assistant": {
        "openwork": ["workspace_name", "window_context"],
    },
    # CLASS: game launcher / game. Which game, and is it running or in a menu.
    "game": {
        "steam": ["store_page_url", "game_name", "game_running", "session_state", "time_in_game"],
        "epic_games_launcher": ["store_page_url", "game_name", "game_running"],
        "riot_client": ["store_page_url", "game_name", "game_running"],
        "battledotnet": ["game_name", "game_running"],
        "ea_app": ["game_name", "game_running"],
        "gog_galaxy": ["game_name", "game_running"],
        "minecraft": ["server_name_or_singleplayer", "session_state", "time_in_game"],
        "roblox": ["experience_name", "game_running"],
        # alternative store / installer front ends
        "heroic": ["store_page_url", "game_name", "game_running"],
        # game streaming front ends (Moonlight, GeForce Now style)
        "moonlight": ["host_name", "game_name", "connection_state"],
        "lutris": ["game_name", "game_running"],
        "balatro": ["game_name", "session_state"],
        "cookie_clicker": ["game_name", "session_state"],
        "arknights_endfield": ["game_name", "session_state"],
        "gryph2": ["game_name", "session_state"],
        "hoyoplay": ["game_name", "session_state"],
        "prismlauncher": ["game_name", "session_state"],
        # Windows store fronts, launchers and individual games
        "ubisoft_connect": ["store_page_url", "game_name", "game_running"],
        "itch": ["game_name", "game_running"],
        "xbox_app": ["game_name", "game_running"],
        "runelite": ["game_name", "session_state"],
        "osu": ["game_name", "session_state"],
        "retroarch": ["game_name", "session_state"],
        "solitaire": ["game_name", "session_state"],
        "steam_big_picture": ["game_name", "game_running"],
    },
    # CLASS: chat / social (native apps). The app is usually the distraction
    # itself, so only the basics plus whether a call is active.
    "chat": {
        "discord": ["server_or_dm_name", "voice_call_active", "streaming"],
        "telegram": ["chat_name"],
        "whatsapp": ["chat_name"],
        "signal": ["chat_name"],
        "vesktop": ["server_or_dm_name", "voice_call_active", "streaming"],
        # Windows chat clients
        "wechat": ["chat_name"],
        "line": ["chat_name"],
        "viber": ["chat_name", "call_active"],
        "messenger": ["chat_name"],
        "threema": ["chat_name"],
        "element": ["server_or_dm_name"],
        "hexchat": ["channel_name"],
        "ferdium": ["server_or_dm_name"],
        "trillian": ["chat_name"],
        "revolt": ["server_or_dm_name"],
        "session": ["chat_name"],
    },
    # Workplace/school collaboration tools have chat UIs but are designed for
    # coordinated work, shared files, meetings, and project communication.
    "collaboration": {
        "teams": ["workspace_name", "channel_or_chat_name", "call_active"],
        "slack": ["workspace_name", "channel_name", "huddle_active"],
        "google_chat": ["workspace_name", "space_or_chat_name"],
        "mattermost": ["workspace_name", "channel_name"],
        "rocketchat": ["workspace_name", "channel_name"],
        "zulip": ["workspace_name", "stream_name"],
        # Windows meetings and workspaces
        "zoom": ["workspace_name", "meeting_name", "call_active"],
        "webex": ["workspace_name", "meeting_name", "call_active"],
        "gotomeeting": ["workspace_name", "meeting_name", "call_active"],
        "bluejeans": ["workspace_name", "meeting_name", "call_active"],
    },
    # CLASS: project trackers and collaborative docs. On Windows these are
    # where a goal's tasks usually live, so they are kept apart from chat.
    "project": {
        "jira": ["workspace_name", "board_name", "issue_key"],
        "linear": ["workspace_name", "issue_key"],
        "trello": ["workspace_name", "board_name"],
        "asana": ["workspace_name", "project_name"],
        "monday": ["workspace_name", "board_name"],
        "clickup": ["workspace_name", "space_name"],
        "basecamp": ["workspace_name", "project_name"],
        "height": ["workspace_name", "project_name"],
        "shortcut": ["workspace_name", "story_name"],
        "plane": ["workspace_name", "issue_key"],
        "obsidian_sync": ["workspace_name"],
        "miro": ["workspace_name", "board_name"],
    },
    # Mail clients are deliberately separate from chat so quick-check-in
    # treatment only applies to messaging/call apps.
    "email": {
        "thunderbird": ["mail_subject", "mail_folder"],
        "kmail": ["mail_subject", "mail_folder"],
        "evolution": ["mail_subject", "mail_folder"],
        "geary": ["mail_subject", "mail_folder"],
        "mailspring": ["mail_subject", "mail_folder"],
        "claws_mail": ["mail_subject", "mail_folder"],
        # Windows mail clients
        "outlook": ["mail_subject", "mail_folder"],
        "outlook_classic": ["mail_subject", "mail_folder"],
        "windows_mail": ["mail_subject", "mail_folder"],
        "mailbird": ["mail_subject", "mail_folder"],
        "the_bat": ["mail_subject", "mail_folder"],
        "postbox": ["mail_subject", "mail_folder"],
    },
    # CLASS: document editor / productivity (native apps).
    "document": {
        "word": ["document_name", "doc_focus_seconds"],
        "excel": ["workbook_name", "sheet_name"],
        "powerpoint": ["presentation_name", "slide_number"],
        "onenote": ["notebook_name", "page_title"],
        "obsidian": ["vault_name", "file_path"],
        "libreoffice": ["document_name", "doc_focus_seconds"],
        "texstudio": ["file_path"],
        # document *viewer*, same treatment
        "okular": ["document_name", "file_path", "page_number"],
        # Windows documents, PDFs and reference managers
        "acrobat": ["document_name", "file_path", "page_number"],
        "acrobat_reader": ["document_name", "file_path", "page_number"],
        "sumatra_pdf": ["document_name", "file_path", "page_number"],
        "calibre": ["document_name", "file_path"],
        "zotero": ["document_name", "collection_name"],
        "visio": ["document_name", "file_path"],
        "ms_project": ["document_name"],
        "publisher": ["document_name", "file_path"],
        "ms_access": ["document_name", "file_path"],
        "todo": ["task_list_name"],
        "evernote": ["notebook_name", "note_title"],
        "drawio": ["document_name", "file_path"],
    },
    # Terminal emulators have their own category: shell work is a common part
    # of coding workflows, not just a generic desktop utility.
    "terminal": {
        "konsole": ["tab_name", "session_context"],
        "xterm": ["tab_name", "session_context"],
        "uxterm": ["tab_name", "session_context"],
        "gnome_terminal": ["tab_name", "session_context"],
        "ptyxis": ["tab_name", "session_context"],
        "kitty": ["tab_name", "session_context"],
        "alacritty": ["tab_name", "session_context"],
        "wezterm": ["tab_name", "session_context"],
        "foot": ["tab_name", "session_context"],
        "tilix": ["tab_name", "session_context"],
        "terminator": ["tab_name", "session_context"],
        "xfce4_terminal": ["tab_name", "session_context"],
        "mate_terminal": ["tab_name", "session_context"],
        "lxterminal": ["tab_name", "session_context"],
        "ghostty": ["tab_name", "session_context"],
        "contour": ["tab_name", "session_context"],
        "rio": ["tab_name", "session_context"],
        # Windows shells and terminal emulators. Their window title is the
        # running program, which is the useful bit on both platforms.
        "windows_terminal": ["tab_name", "session_context"],
        "powershell": ["tab_name", "session_context"],
        "cmd": ["tab_name", "session_context"],
        "conhost": ["tab_name", "session_context"],
        "git_bash": ["tab_name", "session_context"],
        "wsl": ["tab_name", "session_context"],
        "cygwin": ["tab_name", "session_context"],
        "tabby": ["tab_name", "session_context"],
        "warp": ["tab_name", "session_context"],
        "hyper": ["tab_name", "session_context"],
        "busybox_w32": ["tab_name", "session_context"],
    },
    # Creative tools can support some goals (e.g. a lab-report figure), but
    # aren't on-task just because they're productive software.
    "creative": {
        "krita": ["document_name", "canvas_name"],
        "cura_slicer": ["model_name", "printer_profile"],
        # Windows creative and engineering tools
        "photoshop": ["document_name", "file_path"],
        "illustrator": ["document_name", "file_path"],
        "indesign": ["document_name", "file_path"],
        "premiere_pro": ["document_name", "sequence_name"],
        "after_effects": ["document_name", "composition_name"],
        "blender": ["document_name", "scene_name"],
        "davinci_resolve": ["project_name", "page_name"],
        "obs_studio": ["scene_name", "recording_state"],
        "figma": ["document_name", "page_name"],
        "gimp": ["document_name", "file_path"],
        "inkscape": ["document_name", "file_path"],
        "paint": ["image_name"],
        "photos": ["image_name"],
        "canva": ["document_name", "design_name"],
        "clip_studio_paint": ["document_name", "canvas_name"],
        "sketchup": ["model_name", "file_path"],
        "fusion_360": ["document_name", "file_path"],
        "tinkercad": ["design_name"],
        "maya": ["scene_name", "file_path"],
        "cinema_4d": ["file_path", "scene_name"],
    },
    # CLASS: desktop utilities — the apps standard to a computer: file
    # manager, settings, information tools, system utilities. KDE for now;
    # GNOME / other DEs / Windows / macOS later.
    "desktop": {
        # file manager: which folder the user is in is the main signal
        "dolphin": ["location_path", "location_name"],
        # settings apps: which panel they have open
        "systemsettings": ["panel_name"],
        "kinfocenter": ["panel_name"],
        # image viewer / editor
        "gwenview": ["image_name"],
        "kolourpaint": ["image_name"],
        # git front end: which repo is open
        "github-desktop": ["repo_name"],
        # vpn front end: which panel
        "nordvpn": ["panel_name"],
        # archive manager
        "ark": ["archive_name"],
        # screenshot tool: which capture mode is being prepared
        "spectacle": ["capture_mode"],
        # software store: which store page
        "discover": ["page_name", "package_name"],
        # device pairing tool
        "kdeconnect": ["device_name", "connection_state"],
        # disk tool
        "partitionmanager": ["disk_name", "action_state"],
        "windscribe": ["panel_name"],
        "htop": ["process_name"],
        "barrier": ["tool_name"],
        "input_leap": ["tool_name"],
        "protonplus": ["tool_name"],
        "protonup_qt": ["tool_name"],
        "clicker": ["tool_name"],
        # Windows shell / system utilities. Store and framework windows run
        # under ApplicationFrameHost.exe, so these are matched by window title.
        "explorer": ["location_path", "location_name"],
        "settings": ["panel_name"],
        "control_panel": ["panel_name"],
        "task_manager": ["process_name"],
        "registry_editor": ["key_name"],
        "services": ["service_name"],
        "system_info": ["report_name"],
        "device_manager": ["device_name"],
        "disk_management": ["disk_name", "action_state"],
        "disk_cleanup": ["tool_name"],
        "defragment": ["tool_name"],
        "snipping_tool": ["capture_mode"],
        "calculator": ["tool_name"],
        "store": ["page_name", "package_name"],
        "photo_viewer": ["image_name"],
        "recycle_bin": ["tool_name"],
        "windows_security": ["panel_name"],
        "xbox_app_settings": ["panel_name"],
        "clipboard_history": ["tool_name"],
        "ease_of_access": ["panel_name"],
        "maps": ["tool_name"],
        "weather": ["tool_name"],
        "camera": ["tool_name"],
        "sound_recorder": ["capture_mode"],
        "terminal_admin": ["tab_name", "session_context"],
        # Windows network / VPN front ends
        "protonvpn": ["panel_name"],
        "expressvpn": ["panel_name"],
        "mullvad": ["panel_name"],
        "tailscale": ["panel_name"],
        "cloudflare_warp": ["panel_name"],
        "zerotier": ["panel_name"],
        # run dialog / calculator / clipboard helpers: name only
        "_default": ["tool_name"],
    },
}

# WEBAPP_METADATA: sites that aren't apps — they run inside one of the
# browsers above. When the focused browser's site_domain matches one of
# these keys, its fields are added on top of the browser's own fields.
WEBAPP_METADATA = {
    # Video / streaming sites.
    "youtube.com": ["video_title", "channel_name", "video_category", "tab_focus_seconds"],
    "youtube_music": ["track_title", "channel_name", "tab_focus_seconds"],  # music.youtube.com
    "netflix.com": ["show_title", "playback_state", "tab_focus_seconds"],
    "disneyplus.com": ["show_title", "playback_state", "tab_focus_seconds"],
    "twitch.tv": ["streamer_name", "stream_title", "stream_category", "tab_focus_seconds"],
    "spotify.com": ["track_title", "playlist_name", "playback_state"],      # open.spotify.com
    # Social / feeds.
    "x.com": ["feed_type", "profile_viewing"],                              # twitter/x
    "instagram.com": ["reels_active", "dm_active"],
    "tiktok.com": ["video_count_this_session", "session_state"],
    "reddit.com": ["subreddit", "post_title", "site_url", "tab_focus_seconds"],
    "facebook.com": ["feed_type", "chat_name"],
    "snapchat.com": ["chat_name"],
    # Documents / productivity sites.
    "docs.google.com": ["document_name", "editor_active", "tab_focus_seconds"],
    "notion.so": ["page_name", "page_type", "tab_focus_seconds"],
    "notion.com": ["page_name", "page_type", "tab_focus_seconds"],
    "overleaf.com": ["project_name", "file_path", "tab_focus_seconds"],
    "classroom.google.com": ["class_name", "assignment_name"],
    "drive.google.com": ["workspace_name", "file_name", "folder_name"],
    "teams.microsoft.com": ["workspace_name", "channel_name"],
    "teams.live.com": ["workspace_name", "channel_name"],
    "chat.google.com": ["workspace_name", "space_or_chat_name"],
    "app.slack.com": ["workspace_name", "channel_name"],
    "slack.com": ["workspace_name", "channel_name"],
    "onedrive.live.com": ["file_name", "folder_name"],
    "onedrive.com": ["file_name", "folder_name"],
    "word.office.com": ["document_name", "document_state"],
    "office.com": ["workspace_name", "document_name"],
    "microsoft365.com": ["workspace_name", "document_name"],
    "sharepoint.com": ["site_name", "document_name"],
    # Authentication pages are workflow transitions, not destinations.
    "login.microsoftonline.com": ["identity_provider", "authentication_context"],
    "login.live.com": ["identity_provider", "authentication_context"],
    "accounts.google.com": ["identity_provider", "authentication_context"],
    # General-purpose conversational AI websites. Classification describes
    # the site type only; the conversation itself determines relevance.
    "chatgpt.com": ["ai_provider", "conversation_title"],
    "chat.openai.com": ["ai_provider", "conversation_title"],
    "claude.ai": ["ai_provider", "conversation_title"],
    "chat.deepseek.com": ["ai_provider", "conversation_title"],
    "gemini.google.com": ["ai_provider", "conversation_title"],
    "copilot.microsoft.com": ["ai_provider", "conversation_title"],
    "perplexity.ai": ["ai_provider", "conversation_title"],
    "grok.com": ["ai_provider", "conversation_title"],
    "chat.mistral.ai": ["ai_provider", "conversation_title"],
    "poe.com": ["ai_provider", "conversation_title"],
    "chat.qwen.ai": ["ai_provider", "conversation_title"],
    "meta.ai": ["ai_provider", "conversation_title"],
    "kimi.com": ["ai_provider", "conversation_title"],
    "chat.z.ai": ["ai_provider", "conversation_title"],
    "you.com": ["ai_provider", "conversation_title"],
    "character.ai": ["ai_provider", "conversation_title"],
    # Webmail is a separate category from chat, despite running in a browser.
    "mail.google.com": ["mail_provider", "mail_subject"],
    "outlook.live.com": ["mail_provider", "mail_subject"],
    "outlook.office.com": ["mail_provider", "mail_subject"],
    "outlook.office365.com": ["mail_provider", "mail_subject"],
    "mail.yahoo.com": ["mail_provider", "mail_subject"],
    "mail.proton.me": ["mail_provider", "mail_subject"],
    "app.fastmail.com": ["mail_provider", "mail_subject"],
    "mail.zoho.com": ["mail_provider", "mail_subject"],
    "mail.aol.com": ["mail_provider", "mail_subject"],
    # Generic site / not in the list above: browser fields only.
    "_default": [],
}

EMAIL_WEBAPP_PROVIDERS = {
    "mail.google.com": "Gmail",
    "outlook.live.com": "Outlook",
    "outlook.office.com": "Outlook",
    "outlook.office365.com": "Outlook",
    "mail.yahoo.com": "Yahoo Mail",
    "mail.proton.me": "Proton Mail",
    "app.fastmail.com": "Fastmail",
    "mail.zoho.com": "Zoho Mail",
    "mail.aol.com": "AOL Mail",
}
WEBAPP_CATEGORIES = {key: "email" for key in EMAIL_WEBAPP_PROVIDERS}
WEBAPP_CATEGORIES.update({
    "youtube.com": "video",
    "youtube_music": "video",
    "netflix.com": "video",
    "disneyplus.com": "video",
    "twitch.tv": "video",
    "spotify.com": "audio_media",
    "drive.google.com": "file_storage",
    "teams.microsoft.com": "collaboration",
    "teams.live.com": "collaboration",
    "chat.google.com": "collaboration",
    "app.slack.com": "collaboration",
    "slack.com": "collaboration",
    "onedrive.live.com": "file_storage",
    "onedrive.com": "file_storage",
    "word.office.com": "document_editor",
    "office.com": "office_productivity",
    "microsoft365.com": "office_productivity",
    "sharepoint.com": "collaboration",
    "login.microsoftonline.com": "authentication",
    "login.live.com": "authentication",
    "accounts.google.com": "authentication",
})
AI_CHAT_WEBAPP_PROVIDERS = {
    "chatgpt.com": "ChatGPT",
    "chat.openai.com": "ChatGPT",
    "claude.ai": "Claude",
    "chat.deepseek.com": "DeepSeek",
    "gemini.google.com": "Gemini",
    "copilot.microsoft.com": "Microsoft Copilot",
    "perplexity.ai": "Perplexity",
    "grok.com": "Grok",
    "chat.mistral.ai": "Mistral Le Chat",
    "poe.com": "Poe",
    "chat.qwen.ai": "Qwen Chat",
    "meta.ai": "Meta AI",
    "kimi.com": "Kimi",
    "chat.z.ai": "Z.ai Chat",
    "you.com": "You.com AI",
    "character.ai": "Character.AI",
}
AI_CHAT_WEBAPP_DESCRIPTIONS = {
    "chatgpt.com": "ChatGPT is a conversational AI service for questions, explanations, writing, and analysis.",
    "chat.openai.com": "ChatGPT is a conversational AI service for questions, explanations, writing, and analysis.",
    "claude.ai": "Claude is a conversational AI service for questions, writing, analysis, and interactive work with text and files.",
    "chat.deepseek.com": "DeepSeek is a conversational AI service for questions, reasoning, and text generation.",
    "gemini.google.com": "Gemini is a conversational AI service for questions, explanations, writing, and analysis.",
    "copilot.microsoft.com": "Microsoft Copilot is a conversational AI service for questions, writing, and general assistance.",
    "perplexity.ai": "Perplexity is a conversational answer and research service that can provide cited web sources.",
    "grok.com": "Grok is a conversational AI service for questions, analysis, and text generation.",
    "chat.mistral.ai": "Mistral Le Chat is a conversational AI service for questions, writing, and analysis.",
    "poe.com": "Poe is a conversational platform for interacting with multiple AI assistants and models.",
    "chat.qwen.ai": "Qwen Chat is a conversational AI service for questions, explanations, and text generation.",
    "meta.ai": "Meta AI is a conversational assistant for questions and content generation.",
    "kimi.com": "Kimi is a conversational AI service for questions, reasoning, and work with long documents.",
    "chat.z.ai": "Z.ai Chat is a conversational AI service for questions, reasoning, and text generation.",
    "you.com": "You.com provides conversational AI answers and search assistance.",
    "character.ai": "Character.AI provides conversational experiences with user-created AI characters.",
}
WEBAPP_DESCRIPTIONS = {
    "youtube.com": "YouTube hosts educational, instructional, informational, and entertainment videos; relevance depends on the active video or search.",
    "drive.google.com": "Google Drive is a cloud file-storage and file-organization service.",
    "teams.microsoft.com": "Microsoft Teams is a school/work collaboration service for class or team chats, meetings, shared files, assignment information, and project coordination.",
    "teams.live.com": "Microsoft Teams is a school/work collaboration service for class or team chats, meetings, shared files, assignment information, and project coordination.",
    "chat.google.com": "Google Chat is a school/work collaboration service for direct messages, group spaces, and work coordination.",
    "app.slack.com": "Slack is a workplace collaboration service for team channels, direct messages, meetings, and shared work.",
    "slack.com": "Slack is a workplace collaboration service for team channels, direct messages, meetings, and shared work.",
    "onedrive.live.com": "OneDrive is Microsoft's cloud file-storage and file-organization service.",
    "onedrive.com": "OneDrive is Microsoft's cloud file-storage and file-organization service.",
    "word.office.com": "Word for the web is an online document editor.",
    "office.com": "Microsoft 365 is a web portal for accessing Office apps and files.",
    "microsoft365.com": "Microsoft 365 is a web portal for accessing Office apps and files.",
    "sharepoint.com": "SharePoint provides team sites, shared files, and document workspaces.",
    "login.microsoftonline.com": "Microsoft sign-in page used to authenticate to Microsoft services.",
    "login.live.com": "Microsoft account sign-in page used to authenticate to Microsoft services.",
    "accounts.google.com": "Google account sign-in page used to authenticate to Google services.",
}
WEBAPP_DESCRIPTIONS.update({
    domain: "%s is a web email client." % provider
    for domain, provider in EMAIL_WEBAPP_PROVIDERS.items()
})
WEBAPP_DESCRIPTIONS.update(AI_CHAT_WEBAPP_DESCRIPTIONS)
WEBAPP_CATEGORIES.update({key: "ai_chat" for key in AI_CHAT_WEBAPP_PROVIDERS})

# AT-SPI app names that belong to the desktop shell itself, not to a
# user's app; never report them as the focused app.
SHELL_APPS = {
    "kwin", "ksmserver", "plasmashell", "kded6", "kaccess", "ksecretd",
    "xembedsniproxy", "gmenudbusmenuproxy", "ActivityManager", "kwalletd",
    "polkit-kde-authentication-agent-1", "org_kde_powerdevil",
    "xdg-desktop-portal-kde", "xdg-desktop-portal-gtk", "kdeconnect.daemon",
    "xwaylandvideobridge", "kdeconnectd", "discover.notifier", "baloorunner",
    "gcdemu", " kvm", "shell", "org.gnome.Shell", "gnome-shell",
}

# Window-title suffixes -> canonical APP_METADATA key, so
# "Page title - Chromium" identifies the app as a browser even if the
# AT-SPI app id looks odd, and gives us the active tab's title.
TITLE_SUFFIXES = {
    "google chrome": "chrome",
    "chrome canary": "chrome",
    "chromium": "chromium",
    "mozilla firefox": "firefox",
    "firefox": "firefox",
    "microsoft edge": "edge",
    "brave": "brave",
    "opera gx": "opera",
    "opera": "opera",
    "arc": "arc",
    "vivaldi": "vivaldi",
    "safari": "safari",
    "visual studio code": "code",
    "thunderbird": "thunderbird",
    "mozilla thunderbird": "thunderbird",
    "kmail": "kmail",
    "evolution": "evolution",
    "geary": "geary",
    "mailspring": "mailspring",
    "claws mail": "claws_mail",
    "microsoft teams": "teams",
    "teams": "teams",
    "uxterm": "uxterm",
    "konsole": "konsole",
    "xterm": "xterm",
    "gnome terminal": "gnome_terminal",
    "gnome-terminal": "gnome_terminal",
    "ptyxis": "ptyxis",
    "kitty": "kitty",
    "alacritty": "alacritty",
    "wezterm": "wezterm",
    "foot": "foot",
    "tilix": "tilix",
    "terminator": "terminator",
    "xfce terminal": "xfce4_terminal",
    "mate terminal": "mate_terminal",
    "lxterminal": "lxterminal",
    "ghostty": "ghostty",
    "contour": "contour",
    "rio": "rio",
    "opencode": "opencode",
    "open code": "opencode",
    "antigravity": "antigravity",
    "zcode": "zcode",
    "zed": "zed",
    "cursor": "cursor",
    "windsurf": "windsurf",
    "codex": "codex",
    "claude code": "claude_code",
    "gemini cli": "gemini_cli",
    "chatgpt": "chatgpt",
    "claude": "claude",
    "deepseek": "deepseek",
    "perplexity": "perplexity",
}

# --- Windows -----------------------------------------------------------------
#
# Everything Windows-specific lives below this line and is only consulted when
# the process is running on Windows (see IS_WINDOWS). The catalog above is the
# Linux behaviour and is never modified for Windows' sake, so a Windows install
# cannot change what is detected on Linux and vice versa.
IS_WINDOWS = sys.platform == "win32"

# Windows window titles follow the same "<what is open> - <app>" convention,
# but the apps are different ones. Checked after TITLE_SUFFIXES, so an app
# that exists on both platforms keeps its existing mapping.
_WINDOWS_TITLE_SUFFIXES = {
    "chrome beta": "chrome",
    "chrome dev": "chrome",
    "microsoft edge beta": "edge",
    "microsoft edge dev": "edge",
    "microsoft edge canary": "edge",
    "new teams": "teams",
    "outlook": "outlook",
    "outlook (preview)": "outlook",
    "windows mail": "windows_mail",
    "windows terminal": "windows_terminal",
    "windows powershell": "powershell",
    "powershell": "powershell",
    "command prompt": "cmd",
    "git bash": "git_bash",
    "ubuntu": "wsl",
    "windows subsystem for linux": "wsl",
    "notepad": "notepad",
    "notepad++": "notepad++",
    "word": "word",
    "excel": "excel",
    "powerpoint": "powerpoint",
    "onenote": "onenote",
    "acrobat reader": "acrobat_reader",
    "acrobat": "acrobat",
    "adobe acrobat": "acrobat",
    "obsidian": "obsidian",
    "slack": "slack",
    "discord": "discord",
    "telegram": "telegram",
    "whatsapp": "whatsapp",
    "signal": "signal",
    "zoom meeting": "zoom",
    "zoom workplace": "zoom",
    "zoom": "zoom",
    "webex": "webex",
    "figma": "figma",
    "notion": "notion",
    "jira": "jira",
    "linear": "linear",
    "trello": "trello",
    "steam": "steam",
    "epic games launcher": "epic_games_launcher",
    "battle.net": "battledotnet",
    "blender": "blender",
    "adobe photoshop": "photoshop",
    "adobe illustrator": "illustrator",
    "adobe premiere pro": "premiere_pro",
    "adobe after effects": "after_effects",
    "obs studio": "obs_studio",
    "krita": "krita",
    "gimp": "gimp",
    "inkscape": "inkscape",
    "spotify": "spotify",
    "vlc media player": "vlc",
    "windows terminal preview": "windows_terminal",
    "file explorer": "explorer",
    "visual studio 2022": "visual_studio",
    "visual studio": "visual_studio",
}

# Windows executable names (without .exe) and window class names -> canonical
# APP_METADATA key. Windows has no StartupWMClass/desktop files to match on,
# so the process image name is the identity; these map it onto the catalog.
_WINDOWS_APP_ALIASES = {
    # browsers
    "msedge": "edge",
    "microsoftedge": "edge",
    "edge": "edge",
    "brave": "brave",
    "opera": "opera",
    "vivaldi": "vivaldi",
    "chrome": "chrome",
    "chromium": "chromium",
    "firefox": "firefox",
    "yandex": "yandex",
    "browser": "yandex",
    "iridium": "iridium",
    "comodo": "comodo_dragon",
    "dragon": "comodo_dragon",
    "epic": "epic_privacy_browser",
    "tor": "tor_browser",
    "torbrowser": "tor_browser",
    "firefoxdeveloperedition": "firefox",
    "librewolf": "librewolf",
    "waterfox": "waterfox",
    "zen": "zen",
    # editors / IDEs
    "code": "code",
    "code insiders": "code",
    "cursor": "cursor",
    "windsurf": "windsurf",
    "zed": "zed",
    "trae": "trae",
    "notepad": "notepad",
    "notepad++": "notepad++",
    "sublime_text": "sublime_text",
    "sublime text": "sublime_text",
    "devenv": "visual_studio",
    "visual studio": "visual_studio",
    "pycharm": "pycharm",
    "pycharm64": "pycharm",
    "idea": "intellij_idea",
    "idea64": "intellij_idea",
    "webstorm": "webstorm",
    "webstorm64": "webstorm",
    "rider": "rider",
    "rider64": "rider",
    "goland": "goland",
    "phpstorm": "phpstorm",
    "clion": "clion",
    "studio": "android_studio",
    "studio64": "android_studio",
    "nvim": "neovim",
    "vim": "vim",
    "emacs": "emacs",
    "idle": "idle",
    "arduino": "arduino",
    "hxcpp": "hxd",
    "hxd": "hxd",
    "brackets": "brackets",
    "dw": "dreamweaver",
    "texstudio": "texstudio",
    "krita": "krita",
    "kritad": "krita",
    "slic3r": "cura_slicer",
    "cura": "cura_slicer",
    # terminals
    "windowsterminal": "windows_terminal",
    "wt": "windows_terminal",
    "powershell": "powershell",
    "pwsh": "powershell",
    "powershell_ise": "powershell",
    "cmd": "cmd",
    "conhost": "conhost",
    "openconsole": "conhost",
    "bash": "git_bash",
    "git-bash": "git_bash",
    "gitbash": "git_bash",
    "ubuntu": "wsl",
    "wsl": "wsl",
    "cygwin": "cygwin",
    "tabby": "tabby",
    "warp": "warp",
    "hyper": "hyper",
    "wezterm": "wezterm",
    "alacritty": "alacritty",
    "kitty": "kitty",
    "ghostty": "ghostty",
    # office / documents
    "winword": "word",
    "excel": "excel",
    "powerpnt": "powerpoint",
    "onenote": "onenote",
    "msaccess": "ms_access",
    "mspub": "publisher",
    "msproject": "ms_project",
    "visio": "visio",
    "acrobat": "acrobat",
    "acrobat reader": "acrobat_reader",
    "acrord32": "acrobat_reader",
    "sumatrapdf": "sumatra_pdf",
    "calibre": "calibre",
    "zotero": "zotero",
    "obsidian": "obsidian",
    "evernote": "evernote",
    "drawio": "drawio",
    "todo": "todo",
    "outlook": "outlook",
    "olk": "outlook",
    "outlookclassic": "outlook_classic",
    "mailbird": "mailbird",
    "postbox": "postbox",
    "thunderbird": "thunderbird",
    "windowsmail": "windows_mail",
    "thebat": "the_bat",
    # media
    "vlc": "vlc",
    "mpv": "mpv",
    "mpc-hc": "mpc_hc",
    "mpc-hc64": "mpc_hc",
    "potplayer": "potplayer",
    "potplayermini64": "potplayer",
    "kodi": "kodi",
    "wmplayer": "windows_media_player",
    "mediaplayer": "media_player",
    "moviesandtv": "media_player",
    "spotify": "spotify",
    "foobar2000": "foobar2000",
    "musicbee": "musicbee",
    "aimp": "aimp",
    "winamp": "winamp",
    "itunes": "itunes",
    # creative
    "photoshop": "photoshop",
    "illustrator": "illustrator",
    "indesign": "indesign",
    "premierepro": "premiere_pro",
    "aftereffects": "after_effects",
    "blender": "blender",
    "davinciresolve": "davinci_resolve",
    "resolve": "davinci_resolve",
    "obs64": "obs_studio",
    "obs32": "obs_studio",
    "figma": "figma",
    "gimp": "gimp",
    "inkscape": "inkscape",
    "mspaint": "paint",
    "paint": "paint",
    "photos": "photos",
    "microsoftsketchapp": "paint",
    "canva": "canva",
    "clip_studio": "clip_studio_paint",
    "clipstudio": "clip_studio_paint",
    "sketchup": "sketchup",
    "fusion360": "fusion_360",
    "tinkercad": "tinkercad",
    "maya": "maya",
    "c4d": "cinema_4d",
    # chat / collaboration / project tools
    "discord": "discord",
    "discordcanary": "discord",
    "vesktop": "vesktop",
    "telegram": "telegram",
    "whatsapp": "whatsapp",
    "signal": "signal",
    "wechat": "wechat",
    "line": "line",
    "viber": "viber",
    "threema": "threema",
    "element": "element",
    "hexchat": "hexchat",
    "ferdium": "ferdium",
    "trillian": "trillian",
    "revolt": "revolt",
    "session": "session",
    "slack": "slack",
    "ms-teams": "teams",
    "teams": "teams",
    "msteams": "teams",
    "zoom": "zoom",
    "zoomit": "zoom",
    "webex": "webex",
    "CiscoWebex": "webex",
    "gotomeeting": "gotomeeting",
    "bluejeans": "bluejeans",
    "jira": "jira",
    "jira software": "jira",
    "jiracloud": "jira",
    "linear": "linear",
    "trello": "trello",
    "asana": "asana",
    "monday": "monday",
    "monday.com": "monday",
    "clickup": "clickup",
    "basecamp": "basecamp",
    "height": "height",
    "shortcut": "shortcut",
    "plane": "plane",
    "miro": "miro",
    "notion": "notion",
    # AI clients
    "chatgpt": "chatgpt",
    "claude": "claude",
    "deepseek": "deepseek",
    "gemini": "gemini",
    "perplexity": "perplexity",
    "grok": "grok",
    "copilot": "copilot",
    "chatbox": "chatbox",
    "msty": "msty",
    "jan": "jan",
    "lm studio": "lm_studio",
    "cherrystudio": "cherry_studio",
    "anythingllm": "anything_llm",
    "anything-llm": "anything_llm",
    "open-webui": "open_webui",
    "gpt4all": "gpt4all",
    "llamacpp": "llamacpp",
    "chatwise": "chatwise",
    "betterchatgpt": "better_chatgpt",
    "monica": "monica",
    "quark": "quark",
    "doubao": "doubao",
    "erniebot": "ernie_bot",
    "opencode": "opencode",
    "antigravity": "antigravity",
    "zcode": "zcode",
    "trae": "trae",
    "codebuddy": "codebuddy",
    "qoder": "qoder",
    "aider": "aider",
    # games
    "steam": "steam",
    "steamwebhelper": "steam",
    "steam_big_picture": "steam_big_picture",
    "epicgameslauncher": "epic_games_launcher",
    "epicgameslauncher64": "epic_games_launcher",
    "battle.net": "battledotnet",
    "battlenet": "battledotnet",
    "riotclientservices": "riot_client",
    "origin": "ea_app",
    "eabackgroundservice": "ea_app",
    "ubisoftconnect": "ubisoft_connect",
    "upc": "ubisoft_connect",
    "itch": "itch",
    "gamelaunchhelper": "itch",
    "robloxplayerbeta": "roblox",
    "roblox": "roblox",
    "minecraftlauncher": "minecraft",
    "javaw": "minecraft",
    "runelite": "runelite",
    "osu!": "osu",
    "retroarch": "retroarch",
    "solitaire": "solitaire",
    "xbox": "xbox_app",
    "gamepass": "xbox_app",
    "lutris": "lutris",
    "heroic": "heroic",
    "prismlauncher": "prismlauncher",
    # desktop utilities / system
    "explorer": "explorer",
    "control": "control_panel",
    "systemsettings": "settings",
    "taskmgr": "task_manager",
    "regedit": "registry_editor",
    "mmc": "services",
    "services": "services",
    "msinfo32": "system_info",
    "devmgmt": "device_manager",
    "diskmgmt": "disk_management",
    "cleanmgr": "disk_cleanup",
    "snippingtool": "snipping_tool",
    "screenclib": "snipping_tool",
    "calc": "calculator",
    "applicationcalculator": "calculator",
    "winstore": "store",
    "store": "store",
    "wsappxactivationdb": "store",
    "photoviewer": "photo_viewer",
    "recyclebin": "recycle_bin",
    "windowssecurity": "windows_security",
    "easeofaccess": "ease_of_access",
    "clipboard": "clipboard_history",
    "nordvpn": "nordvpn",
    "nordvpn-gui": "nordvpn",
    "windscribe": "windscribe",
    "protonvpn": "protonvpn",
    "expressvpn": "expressvpn",
    "mullvad": "mullvad",
    "tailscale": "tailscale",
    "tailscaled": "tailscale",
    "warp": "cloudflare_warp",
    "cloudflare-warp": "cloudflare_warp",
    "zerotierone": "zerotier",
    "protonup-qt": "protonup_qt",
    "htop": "htop",
    "githubdesktop": "github-desktop",
}

# Windows Store / framework windows all run under ApplicationFrameHost.exe and
# put nothing but their own display name in the window title, so there is no
# process identity to match (and Windows does not export the AppUserModelID
# that would give one). Match the whole title instead - exact, case and
# punctuation insensitive - never a substring, so an ordinary window that
# merely mentions "Settings" is not mistaken for the Settings app.
_WINDOWS_TITLE_APPS = {
    "settings": "settings",
    "network & internet": "settings",
    "bluetooth & devices": "settings",
    "personalization": "settings",
    "apps & features": "settings",
    "system": "settings",
    "time & language": "settings",
    "gaming": "settings",
    "accessibility": "settings",
    "privacy & security": "settings",
    "windows update": "settings",
    "update & security": "settings",
    "control panel": "control_panel",
    "all control panel items": "control_panel",
    "task manager": "task_manager",
    "performance": "task_manager",
    "app history": "task_manager",
    "startup apps": "task_manager",
    "users": "task_manager",
    "details": "task_manager",
    "services": "services",
    "registry editor": "registry_editor",
    "system information": "system_info",
    "device manager": "device_manager",
    "disk management": "disk_management",
    "cleanmgr": "disk_cleanup",
    "disk cleanup": "disk_cleanup",
    "snipping tool": "snipping_tool",
    "screen recorder": "snipping_tool",
    "calculator": "calculator",
    "clock": "calculator",
    "microsoft store": "store",
    "store": "store",
    "library": "store",
    "photos": "photos",
    "paint": "paint",
    "windows security": "windows_security",
    "windows defender firewall with advanced security": "windows_security",
    "windows terminal": "windows_terminal",
    "terminal": "windows_terminal",
    "command prompt": "cmd",
    "powershell": "powershell",
    "windows powershell": "powershell",
    "windows powershell 7": "powershell",
    "administrator: windows powershell": "powershell",
    "administrator: command prompt": "cmd",
    "file explorer": "explorer",
    "home": "explorer",
    "recycle bin": "recycle_bin",
    "ease of access": "ease_of_access",
    "clipboard history": "clipboard_history",
    "xbox": "xbox_app",
    "solitaire collection": "solitaire",
    "news": "media_player",
    "movies & tv": "media_player",
    "get started": "settings",
    "new text document": "notepad",
    "untitled - notepad": "notepad",
    "mail": "windows_mail",
    "calendar": "outlook",
    "people": "outlook",
    "to do": "todo",
    "maps": "maps",
    "weather": "weather",
    "camera": "camera",
    "sound recorder": "sound_recorder",
    "game bar": "xbox_app",
    "media player": "media_player",
    "windows media player": "windows_media_player",
    "notepad (administrator)": "notepad",
    "magnifier": "ease_of_access",
    "narrator": "ease_of_access",
    "on-screen keyboard": "ease_of_access",
    "color": "settings",
    "default apps": "settings",
    "optional features": "settings",
    "recovery": "settings",
    "about": "system_info",
    "task scheduler": "services",
    "event viewer": "services",
    "computer management": "services",
    "local users and groups": "system_info",
    "performance options": "system_info",
}


@functools.lru_cache(maxsize=1)
def _title_suffix_map():
    """(suffix, app key) pairs to try, in order, for this platform.

    Windows adds its own suffixes after the shared ones, so an app that
    exists on both platforms resolves exactly as it does on Linux.
    """
    pairs = tuple(TITLE_SUFFIXES.items())
    if IS_WINDOWS:
        pairs += tuple(_WINDOWS_TITLE_SUFFIXES.items())
    return pairs

# Key: normalized app id -> APP_METADATA key. AT-SPI app names vary
# ("code", "Code", ...) so everything goes through this map first.
APP_ALIASES = {
    "vscode": "code",
    "visual-studio-code": "code",
    "google-chrome": "chrome",
    "microsoft-edge": "edge",
    # package/form ids that don't split into a known token
    "zenbrowser": "zen",
    "waterfox-current": "waterfox",
    "waterfox-classic": "waterfox",
    "nordvpn-gui": "nordvpn",
    "hgl": "heroic",   # Heroic Games Launcher flatpak window class
    "github desktop": "github-desktop",
    "githubdesktop": "github-desktop",
    "claws-mail": "claws_mail",
    "google-antigravity": "antigravity",
    "openai-codex": "codex",
    "codex-cli": "codex",
    "claude-code": "claude_code",
    "claude code": "claude_code",
    "gemini-cli": "gemini_cli",
    "gemini cli": "gemini_cli",
    "roo-code": "roo_code",
    "roocode": "roo_code",
    "github-copilot": "github_copilot",
    "github copilot": "github_copilot",
    "aider-chat": "aider",
    "goose-cli": "goose",
    "chat gpt": "chatgpt",
    "deepseek chat": "deepseek",
    "mistral le chat": "mistral_le_chat",
    "mistral-chat": "mistral_le_chat",
    "qwen chat": "qwen_chat",
    "meta ai": "meta_ai",
    "z.ai chat": "zai_chat",
    "lm studio": "lm_studio",
    "cookie clicker": "cookie_clicker",
    "cookie-clicker": "cookie_clicker",
    "idle-python3.13": "idle",
    "processing-app-base": "arduino",
    "com.differentai.openwork": "openwork",
    "net.lutris.lutris": "lutris",
    "net.lutris.arknights-endfield-3": "arknights_endfield",
    "net.lutris.gryph2-4": "gryph2",
    "net.lutris.hoyoplay-2": "hoyoplay",
    "net.lutris.hoyoplay-5": "hoyoplay",
    "steam_app_2379780": "balatro",
    "steam_app_1454400": "cookie_clicker",
    "steam_app_280680": "krita",
    "cura-slicer": "cura_slicer",
    "prismlauncher-alpo": "prismlauncher",
    "io.github.input_leap.input-leap": "input_leap",
    "input-leap": "input_leap",
    "net.davidotek.pupgui2": "protonup_qt",
    "com.vysp3r.protonplus": "protonplus",
    "com.github.debauchee.barrier": "barrier",
    "net.codelogistics.clicker": "clicker",
    "org.gnome.terminal": "gnome_terminal",
    "org.gnome.ptyxis": "ptyxis",
    "org.alacritty.alacritty": "alacritty",
    "org.wezfurlong.wezterm": "wezterm",
    "com.mitchellh.ghostty": "ghostty",
    "org.kde.konsole": "konsole",
    "xfce4-terminal": "xfce4_terminal",
    "mate-terminal": "mate_terminal",
}

# Short, concrete descriptions are included in app_metadata so Jev does not
# have to infer what a less-common installed program is from its name.
APP_DESCRIPTIONS = {
    "opencode": "OpenCode is an AI coding agent available as a terminal, desktop, and IDE tool for working with software projects.",
    "antigravity": "Antigravity is an agentic software-development environment with an editor and agents that work across code, terminal, and browser.",
    "openwork": "OpenWork is a desktop workspace for running AI agents, skills, and MCP-connected workflows on files and projects.",
    "zcode": "ZCode is a software-development workbench with an integrated coding agent.",
    "zed": "Zed is a code editor with integrated AI-assisted and agentic coding features.",
    "cursor": "Cursor is an AI-focused code editor with coding-agent features.",
    "windsurf": "Windsurf is an AI-focused code editor and agentic software-development environment.",
    "codex": "OpenAI Codex is a coding agent available through terminal, IDE, and desktop integrations.",
    "claude_code": "Claude Code is Anthropic's coding agent for working with software projects through terminal and IDE integrations.",
    "gemini_cli": "Gemini CLI is Google's terminal-based AI coding agent for software-development tasks.",
    "aider": "Aider is a terminal-based AI pair-programming tool that edits software repositories.",
    "goose": "Goose is an extensible AI agent that can use development tools and operate on software projects.",
    "openhands": "OpenHands is an open-source software-development agent platform.",
    "cline": "Cline is an IDE coding-agent extension that can work with files and development tools.",
    "roo_code": "Roo Code is an IDE coding-agent extension with configurable software-development modes.",
    "continue": "Continue is an AI coding assistant and agent integrated with code editors.",
    "github_copilot": "GitHub Copilot is an AI coding assistant integrated into supported code editors.",
    "kiro": "Kiro is an AI-powered IDE and agentic software-development environment.",
    "devin": "Devin is an AI software-engineering agent and development environment.",
    "amp": "Amp is an AI coding agent for software-development tasks.",
    "chatgpt": "ChatGPT is a conversational AI assistant for questions, explanations, writing, and analysis.",
    "claude": "Claude is a conversational AI assistant for questions, writing, analysis, and interactive work with text and files.",
    "deepseek": "DeepSeek is a conversational AI assistant for questions, reasoning, and text generation.",
    "gemini": "Gemini is a conversational AI assistant for questions, explanations, writing, and analysis.",
    "copilot": "Microsoft Copilot is a conversational AI assistant for questions, writing, and general assistance.",
    "perplexity": "Perplexity is a conversational answer and research service that can provide cited web sources.",
    "grok": "Grok is a conversational AI assistant for questions, analysis, and text generation.",
    "mistral_le_chat": "Mistral Le Chat is a conversational AI assistant for questions, writing, and analysis.",
    "poe": "Poe is a conversational platform for interacting with multiple AI assistants and models.",
    "qwen_chat": "Qwen Chat is a conversational AI assistant for questions, explanations, and text generation.",
    "meta_ai": "Meta AI is a conversational assistant for questions and content generation.",
    "kimi": "Kimi is a conversational AI assistant for questions, reasoning, and work with long documents.",
    "zai_chat": "Z.ai Chat is a conversational AI assistant for questions, reasoning, and text generation.",
    "chatbox": "Chatbox is a desktop client for conversations with AI models.",
    "lm_studio": "LM Studio is a desktop application for running local models and chatting with them.",
    "jan": "Jan is a desktop AI assistant for chatting with local and hosted models.",
    "msty": "Msty is a desktop application for conversations with local and hosted AI models.",
    "code": "Visual Studio Code is a source-code editor with a large extension ecosystem, including optional AI coding tools.",
    "arduino": "Arduino IDE is used to write and upload code for Arduino electronics and hardware prototypes.",
    "idle": "Python IDLE is a basic editor and interactive shell for writing and running Python code.",
    "krita": "Krita is a digital painting and illustration program, sometimes used to make figures or diagrams.",
    "cura_slicer": "Ultimaker Cura is a 3D-printing slicer that prepares models for printing.",
    "vesktop": "Vesktop is an alternative Discord desktop client for chat, voice, and streaming.",
    "thunderbird": "Thunderbird is an email and calendar client.",
    "kmail": "KMail is KDE's desktop email client.",
    "evolution": "Evolution is a desktop email, calendar, and contacts client.",
    "geary": "Geary is a desktop email client.",
    "mailspring": "Mailspring is a desktop email client.",
    "claws_mail": "Claws Mail is a lightweight desktop email client.",
    "konsole": "Konsole is KDE's terminal emulator for shell commands and command-line programs.",
    "xterm": "XTerm is a terminal emulator for shell commands and command-line programs.",
    "uxterm": "UXTerm is a Unicode-enabled XTerm terminal emulator.",
    "gnome_terminal": "GNOME Terminal is a terminal emulator for shells and command-line programs.",
    "ptyxis": "Ptyxis is a GNOME terminal emulator for shells and command-line programs.",
    "kitty": "Kitty is a GPU-based terminal emulator for shells and command-line programs.",
    "alacritty": "Alacritty is a terminal emulator for shells and command-line programs.",
    "wezterm": "WezTerm is a terminal emulator and multiplexer for command-line workflows.",
    "foot": "Foot is a Wayland terminal emulator for shells and command-line programs.",
    "tilix": "Tilix is a tiling terminal emulator for command-line workflows.",
    "terminator": "Terminator is a terminal emulator with multiple terminal panes.",
    "xfce4_terminal": "Xfce Terminal is a terminal emulator for shells and command-line programs.",
    "mate_terminal": "MATE Terminal is a terminal emulator for shells and command-line programs.",
    "lxterminal": "LXTerminal is a lightweight terminal emulator for command-line programs.",
    "ghostty": "Ghostty is a terminal emulator for shells and command-line programs.",
    "contour": "Contour is a terminal emulator for command-line workflows.",
    "rio": "Rio is a terminal emulator for shells and command-line programs.",
    "lutris": "Lutris is a launcher and manager for PC games from multiple sources.",
    "balatro": "Balatro is a poker-inspired single-player video game.",
    "cookie_clicker": "Cookie Clicker is an incremental idle game.",
    "arknights_endfield": "Arknights: Endfield is an action role-playing and strategy video game.",
    "gryph2": "gryph2 is a Lutris-managed game entry; the launcher name alone does not identify its current game or activity.",
    "hoyoplay": "HoYoPlay is a launcher for games published by HoYoverse.",
    "prismlauncher": "Prism Launcher manages Minecraft instances, mods, and game launches.",
    "windscribe": "Windscribe is a VPN and network privacy application.",
    "htop": "htop is an interactive system and process monitor.",
    "barrier": "Barrier shares a keyboard and mouse between computers over a network.",
    "input_leap": "Input Leap shares a keyboard and mouse between computers over a network.",
    "protonplus": "ProtonPlus manages compatibility tools used to run games on Linux.",
    "protonup_qt": "ProtonUp-Qt installs and manages Steam Play compatibility tools.",
    "clicker": "Clicker is an auto-clicker that can repeatedly simulate mouse clicks and key presses.",
    "firefox": "Firefox is a web browser; the active page title and URL are more informative than the app name.",
    "chrome": "Google Chrome is a web browser; the active page title and URL are more informative than the app name.",
    "chromium": "Chromium is a web browser; the active page title and URL are more informative than the app name.",
    "opera": "Opera or Opera GX is a web browser; the active page title and URL are more informative than the app name.",
    "discord": "Discord is a chat, voice, and community application; brief task-related messages may be appropriate.",
    "teams": "Microsoft Teams is a school/work collaboration app for class and team channels, shared files, assignment information, meetings, and project coordination.",
    "slack": "Slack is a workplace collaboration app for team channels, direct messages, meetings, and shared work.",
    "google_chat": "Google Chat is a school/work collaboration app for direct messages, group spaces, and work coordination.",
    "mattermost": "Mattermost is a workplace collaboration and team messaging app.",
    "rocketchat": "Rocket.Chat is a team communication and collaboration platform.",
    "zulip": "Zulip is a team collaboration and threaded messaging app.",
    "steam": "Steam is a game store and launcher; its store or library is not itself evidence of goal-related work.",
    "systemsettings": "KDE System Settings configures desktop and system options; the active settings panel identifies the current task.",
    "kinfocenter": "KDE Info Center displays information about the computer's hardware, software, and system status.",
    "dolphin": "Dolphin is KDE's file manager for browsing and managing files and folders.",
    "discover": "KDE Discover is a software center for browsing and managing applications and updates.",
}

# Windows apps. Same purpose as APP_DESCRIPTIONS above: tell Jev what the
# tool is for, so it does not have to infer it from the executable name.
APP_DESCRIPTIONS.update({
    # browsers
    "yandex": "Yandex Browser is a web browser; the active page title and URL are more informative than the app name.",
    "ungoogled_chromium": "Ungoogled Chromium is a web browser; the active page title and URL are more informative than the app name.",
    "iridium": "Iridium is a web browser; the active page title and URL are more informative than the app name.",
    "comodo_dragon": "Comodo Dragon is a web browser; the active page title and URL are more informative than the app name.",
    "epic_privacy_browser": "Epic Privacy Browser is a web browser; the active page title and URL are more informative than the app name.",
    "tor_browser": "Tor Browser is a privacy-focused web browser; the active page title and URL are more informative than the app name.",
    "cent_browser": "Cent Browser is a web browser; the active page title and URL are more informative than the app name.",
    "maxthon": "Maxthon is a web browser; the active page title and URL are more informative than the app name.",
    "coc_coc": "Coc Coc is a web browser; the active page title and URL are more informative than the app name.",
    "avast_secure_browser": "Avast Secure Browser is a web browser; the active page title and URL are more informative than the app name.",
    "ccleaner_browser": "CCleaner Browser is a web browser; the active page title and URL are more informative than the app name.",
    "kinza": "Kinza is a web browser; the active page title and URL are more informative than the app name.",
    "github-desktop": "GitHub Desktop is a git client for reviewing changes, making commits and managing branches and pull requests.",
    # editors and IDEs
    "notepad": "Notepad is Windows' basic plain-text editor, often used for notes and quick text edits.",
    "visual_studio": "Microsoft Visual Studio is an IDE for building software, primarily with C# and .NET.",
    "pycharm": "PyCharm is an IDE for Python software development.",
    "intellij_idea": "IntelliJ IDEA is an IDE for JVM-language software development.",
    "webstorm": "WebStorm is an IDE for web and JavaScript development.",
    "rider": "Rider is an IDE for .NET and C# development.",
    "goland": "GoLand is an IDE for Go development.",
    "phpstorm": "PhpStorm is an IDE for PHP and web development.",
    "clion": "CLion is an IDE for C and C++ development.",
    "android_studio": "Android Studio is the official IDE for Android application development.",
    "textpad": "TextPad is a basic text editor for notes and quick edits.",
    "hxd": "Hxd is a hexadecimal editor for inspecting binary files.",
    "brackets": "Brackets is a code editor aimed at front-end web development.",
    "dreamweaver": "Dreamweaver is a visual web authoring and code editing application.",
    # terminals
    "windows_terminal": "Windows Terminal is a terminal emulator for shells and command-line programs.",
    "powershell": "PowerShell is Windows' command-line shell and scripting language; shell, build, version-control and debugging steps count as command-line work.",
    "cmd": "The Windows command prompt is a command-line shell for running programs and scripts.",
    "conhost": "The Windows console host runs command-line programs in a console window.",
    "git_bash": "Git Bash is a shell environment for Git and Unix-style command-line work.",
    "wsl": "The Windows Subsystem for Linux runs a Linux shell and command-line programs on Windows.",
    "cygwin": "Cygwin is a Unix-like command-line environment for Windows.",
    "tabby": "Tabby is a terminal emulator and multiplexer for command-line workflows.",
    "warp": "Warp is a modern terminal emulator with command-line workflows.",
    "hyper": "Hyper is a terminal emulator for shell and command-line programs.",
    "busybox_w32": "BusyBox for Windows provides small Unix-style command-line utilities.",
    # AI clients
    "cherry_studio": "Cherry Studio is a desktop client for conversations with hosted and local AI models.",
    "anything_llm": "AnythingLLM is a desktop client for chatting with documents and local or hosted AI models.",
    "open_webui": "Open WebUI is a web interface for chatting with local and hosted AI models.",
    "gpt4all": "GPT4All is a desktop application for running and chatting with local language models.",
    "llamacpp": "llama.cpp's desktop front end runs and chats with local language models.",
    "chatwise": "ChatWise is a desktop client for conversations with local and hosted AI models.",
    "better_chatgpt": "Better ChatGPT is a browser extension and front end for ChatGPT conversations.",
    "monica": "Monica is an AI assistant browser extension for chat and writing help.",
    "quark": "Quark is an AI assistant and browser for chat, search and documents.",
    "doubao": "Doubao is a conversational AI assistant.",
    "ernie_bot": "ERNIE Bot is a conversational AI assistant from Baidu.",
    "trae": "Trae is an AI-focused code editor with coding-agent features.",
    "codebuddy": "CodeBuddy is an AI coding assistant integrated into development tools.",
    "qoder": "Qoder is an agentic software-development environment built around a code editor.",
    "augment_code": "Augment Code is an AI coding assistant that indexes and edits software projects.",
    # games
    "ubisoft_connect": "Ubisoft Connect is a game store and launcher; its store or library is not itself evidence of goal-related work.",
    "itch": "itch.io is an indie game store, library and launcher.",
    "xbox_app": "Xbox is a game store and launcher; its library is not itself evidence of goal-related work.",
    "runelite": "RuneLite is a third-party Old School RuneScape client.",
    "osu": "osu! is a rhythm game.",
    "retroarch": "RetroArch is an emulator front end for retro game systems.",
    "solitaire": "Solitaire Collection is Windows' bundled card game collection.",
    "steam_big_picture": "Steam's Big Picture mode is the game store and launcher's console-style interface.",
    # chat / collaboration
    "wechat": "WeChat is a chat and social messaging application.",
    "line": "LINE is a chat and social messaging application.",
    "viber": "Viber is a chat, voice-call and social messaging application.",
    "messenger": "Messenger is a social messaging application.",
    "threema": "Threema is a private chat and messaging application.",
    "element": "Element is a Matrix client for team chat.",
    "hexchat": "HexChat is an IRC client for chat rooms.",
    "ferdium": "Ferdium is a multi-service chat client that groups messages from several services.",
    "trillian": "Trillian is an IRC client.",
    "revolt": "Revolt is a community chat application similar to Discord.",
    "session": "Session is a private messenger that does not need phone numbers.",
    "zoom": "Zoom is a video-conferencing app for meetings, classes and calls; with a school/work goal, normal meeting use is usually on-task.",
    "webex": "Webex is a video-conferencing and collaboration app for meetings and calls.",
    "gotomeeting": "GoToMeeting is a video-conferencing app for meetings and calls.",
    "bluejeans": "BlueJeans is a video-conferencing app for meetings and calls.",
    # project trackers
    "jira": "Jira is a software project tracker for issues, sprints and project work.",
    "linear": "Linear is a software project tracker for issues and project work.",
    "trello": "Trello is a project board of cards grouped into lists.",
    "asana": "Asana is a project and task management workspace.",
    "monday": "monday.com is a project and task management workspace.",
    "clickup": "ClickUp is a project and task management workspace.",
    "basecamp": "Basecamp is a project and to-do management workspace for teams.",
    "height": "Height is a project and task management workspace.",
    "shortcut": "Shortcut is a project and issue tracker for software teams.",
    "plane": "Plane is a project and issue tracker for software teams.",
    "obsidian_sync": "Obsidian Sync is the sync service for Obsidian notes.",
    "miro": "Miro is a collaborative online whiteboard.",
    # mail
    "outlook": "Outlook is Microsoft's email, calendar and contacts client.",
    "outlook_classic": "Outlook is Microsoft's email, calendar and contacts client.",
    "windows_mail": "Windows Mail is Microsoft's email and calendar client.",
    "mailbird": "Mailbird is a desktop email client.",
    "the_bat": "The Bat! is a desktop email client.",
    "postbox": "Postbox is a desktop email client.",
    # documents
    "acrobat": "Adobe Acrobat is a PDF editor and form-filling application.",
    "acrobat_reader": "Adobe Acrobat Reader is a PDF document viewer.",
    "sumatra_pdf": "Sumatra PDF is a lightweight PDF document viewer.",
    "calibre": "Calibre is an ebook library manager, editor and reader.",
    "zotero": "Zotero is a reference manager for collecting and citing research sources.",
    "visio": "Microsoft Visio is a diagramming and process-mapping application.",
    "ms_project": "Microsoft Project is a project planning and scheduling application.",
    "publisher": "Microsoft Publisher is a desktop publishing and document layout application.",
    "ms_access": "Microsoft Access is a desktop database application.",
    "todo": "Microsoft To Do is a personal task and reminder list.",
    "evernote": "Evernote is a note-taking application.",
    "drawio": "draw.io is a diagramming and flowchart editor.",
    # media
    "mpc_hc": "Media Player Classic is a video and audio player.",
    "potplayer": "PotPlayer is a video and audio player.",
    "kodi": "Kodi is a media-center application for playing local and network media.",
    "windows_media_player": "Windows Media Player is a video and audio player.",
    "media_player": "Films & TV is the Windows video player.",
    "spotify": "Spotify is a music streaming service and desktop player.",
    "foobar2000": "foobar2000 is an audio player for local music libraries.",
    "musicbee": "MusicBee is an audio player and music library organizer.",
    "aimp": "AIMP is an audio player.",
    "winamp": "Winamp is an audio player.",
    "itunes": "iTunes is a media library, player and device manager.",
    # creative
    "photoshop": "Adobe Photoshop is an image editor for photo and graphic work.",
    "illustrator": "Adobe Illustrator is a vector graphics editor.",
    "indesign": "Adobe InDesign is a page layout and publishing application.",
    "premiere_pro": "Adobe Premiere Pro is a video editing application.",
    "after_effects": "Adobe After Effects is a motion graphics and compositing application.",
    "blender": "Blender is a 3D modeling, animation and rendering application.",
    "davinci_resolve": "DaVinci Resolve is a video editor with color grading and audio tools.",
    "obs_studio": "OBS Studio is a screen recorder and live-streaming application.",
    "figma": "Figma is a collaborative interface design and prototyping tool.",
    "gimp": "GIMP is a raster image editor.",
    "inkscape": "Inkscape is a vector graphics editor.",
    "paint": "Paint is the basic Windows image editor.",
    "photos": "Windows Photos is the Windows image and video viewer.",
    "canva": "Canva is a web-based graphic design tool.",
    "clip_studio_paint": "Clip Studio Paint is a drawing and illustration application.",
    "sketchup": "SketchUp is a 3D modeling application.",
    "fusion_360": "Fusion 360 is a 3D modeling, CAD and manufacturing application.",
    "tinkercad": "Tinkercad is a browser-based 3D modeling tool for simple designs.",
    "maya": "Maya is a 3D modeling and animation application.",
    "cinema_4d": "Cinema 4D is a 3D modeling and animation application.",
    # desktop utilities
    "explorer": "File Explorer is the Windows file manager; which folder is open is the main signal.",
    "settings": "Windows Settings configures system and device options; the open page identifies the task.",
    "control_panel": "Control Panel configures Windows system options; the open page identifies the task.",
    "task_manager": "Task Manager lists running processes and their resource use.",
    "registry_editor": "Registry Editor browses and edits the Windows registry.",
    "services": "The Windows Services console manages background system services.",
    "system_info": "System Information displays details about the computer's hardware and software.",
    "device_manager": "Device Manager manages hardware devices and their drivers.",
    "disk_management": "Disk Management manages disks, partitions and volumes.",
    "disk_cleanup": "Disk Cleanup removes temporary and unused files to free space.",
    "snipping_tool": "Snipping Tool captures screenshots and screen recordings.",
    "calculator": "Calculator performs arithmetic and unit conversions.",
    "store": "Microsoft Store is a software store for installing and updating applications and games.",
    "photo_viewer": "Windows Photos views images and videos.",
    "recycle_bin": "The Recycle Bin holds deleted files awaiting permanent removal.",
    "windows_security": "Windows Security manages antivirus, firewall and device protection settings.",
    "ease_of_access": "Ease of Access configures Windows accessibility options.",
    "clipboard_history": "Clipboard History shows recent clipboard entries.",
    "maps": "Windows Maps is a maps and navigation app.",
    "weather": "MSN Weather is a weather forecast app.",
    "camera": "Windows Camera is a camera capture app.",
    "sound_recorder": "Sound Recorder captures audio recordings.",
    "xbox_app_settings": "Xbox settings configure the Xbox app and game pass.",
    "terminal_admin": "An elevated Windows shell running command-line programs as administrator.",
    "protonvpn": "Proton VPN is a VPN and network privacy application.",
    "expressvpn": "ExpressVPN is a VPN and network privacy application.",
    "mullvad": "Mullvad VPN is a VPN and network privacy application.",
    "tailscale": "Tailscale is a mesh VPN for connecting devices to each other securely.",
    "cloudflare_warp": "Cloudflare WARP is a VPN and network privacy application.",
    "zerotier": "ZeroTier is a mesh VPN for connecting devices to each other securely.",
})

# Gecko soft-forks keep Firefox's sessionstore format and layout exactly,
# so they get the same browser metadata fields and are routed through the
# same sessionstore reader (see _GECKO_PROFILE_ROOTS for their paths).
for _gecko_fork in ("librewolf", "waterfox", "zen", "floorp"):
    APP_METADATA["browser"][_gecko_fork] = list(APP_METADATA["browser"]["firefox"])
del _gecko_fork

# Every app key defined across all APP_METADATA classes (for _canonical()).
_ALL_APP_KEYS = {key for apps in APP_METADATA.values() for key in apps}

# KWin/AT-SPI class -> the display name the app puts in its own window
# title, so "cranky-clippy — Dolphin" does not report the folder as
# "cranky-clippy".
_DESKTOP_APP_DISPLAY_NAMES = {
    "dolphin": {"dolphin", "file manager"},
    "systemsettings": {"system settings", "settings"},
    "kinfocenter": {"kinfocenter", "info center"},
    "ark": {"ark"},
    "spectacle": {"spectacle"},
    "discover": {"discover", "software center"},
    "kdeconnect": {"kde connect", "kdeconnect"},
    "partitionmanager": {"kde partition manager"},
    "kolourpaint": {"kolourpaint", "kolour paint"},
    "gwenview": {"gwenview"},
    "github-desktop": {"github desktop", "githubdesktop"},
    "nordvpn": {"nordvpn"},
    # Windows: these put only their own name in the window title, so the
    # first title part must not be reported as the "thing being worked on".
    "explorer": {"file explorer", "explorer", "home", "home folder"},
    "settings": {"settings"},
    "control_panel": {"control panel"},
    "task_manager": {"task manager"},
    "registry_editor": {"registry editor"},
    "services": {"services"},
    "system_info": {"system information"},
    "device_manager": {"device manager"},
    "disk_management": {"disk management"},
    "disk_cleanup": {"disk cleanup", "cleanmgr"},
    "snipping_tool": {"snipping tool", "screen recorder"},
    "calculator": {"calculator", "clock"},
    "store": {"microsoft store", "store"},
    "photo_viewer": {"photos", "photo viewer"},
    "recycle_bin": {"recycle bin"},
    "windows_security": {"windows security"},
    "ease_of_access": {"ease of access"},
    "clipboard_history": {"clipboard history"},
    "xbox_app": {"xbox", "game bar"},
    "media_player": {"films & tv", "media player", "news"},
    "windows_media_player": {"windows media player"},
    "paint": {"paint"},
    "photos": {"photos"},
    "windows_terminal": {"windows terminal"},
    "powershell": {"windows powershell", "powershell"},
    "cmd": {"command prompt", "cmd"},
    "git_bash": {"git bash"},
    "wsl": {"ubuntu", "wsl"},
    "outlook": {"outlook", "mail", "calendar", "people"},
    "windows_mail": {"mail"},
    "notepad": {"notepad"},
    "todo": {"to do", "tasks"},
}

# Same idea for game launchers: their window title often carries just the
# launcher's own name, which is not a game.
_GAME_DISPLAY_NAMES = {
    "steam": {"steam"},
    "heroic": {"heroic games launcher", "heroic"},
    "epic_games_launcher": {"epic games launcher", "epic"},
    "riot_client": {"riot client"},
    "battledotnet": {"battle.net", "battle"},
    "gog_galaxy": {"gog galaxy", "gog"},
    "ea_app": {"ea app", "ea"},
    "roblox": {"roblox"},
    "minecraft": {"minecraft launcher"},
    "moonlight": {"moonlight"},
    "lutris": {"lutris"},
    "hoyoplay": {"hoyoplay"},
    "arknights_endfield": {"arknights: endfield", "arknights endfield"},
    # Windows store fronts and launchers
    "ubisoft_connect": {"ubisoft connect", "ubisoft", "uplay"},
    "itch": {"itch.io", "itch"},
    "xbox_app": {"xbox", "game bar", "xbox pc app"},
    "runelite": {"runelite"},
    "osu": {"osu!"},
    "retroarch": {"retroarch"},
    "solitaire": {"solitaire collection", "solitaire"},
    "steam_big_picture": {"steam", "big picture"},
}


# --- Firefox session store ---------------------------------------------------
#
# Snap Firefox is fenced off from AT-SPI by AppArmor, so its URL bar can't
# be read from the accessibility tree. But Firefox — and every Gecko
# soft-fork, which keep the same sessionstore format and layout — writes its
# complete session (all windows, all tabs, current URLs and titles) to
# sessionstore-backups/recovery.jsonlz4 every few seconds, and that file is
# world-readable for the user's own uid. That is the URL source for the
# Gecko family — no accessibility involved. Paths below cover deb/rpm,
# snap and flatpak installs (flatpak dirs also match by id glob).

_GECKO_PROFILE_ROOTS = {
    "firefox": (
        "~/.mozilla/firefox",
        "~/snap/firefox/common/.mozilla/firefox",
        "~/.var/app/org.mozilla.firefox/.mozilla/firefox",
    ),
    "librewolf": (
        "~/.librewolf",
        "~/snap/librewolf/common/.librewolf",
        "~/.var/app/io.gitlab.librewolf-community/.librewolf",
    ),
    "waterfox": (
        "~/.waterfox",
        "~/.var/app/*waterfox*/.waterfox",
    ),
    "zen": (
        "~/.zen",
        "~/.var/app/app.zenbrowser.ZenBrowser/.zen",
    ),
    "floorp": (
        "~/.floorp",
        "~/snap/floorp/common/.floorp",
        "~/.var/app/one.ablaze.floorp/.floorp",
    ),
}

# Windows profile locations. %APPDATA% / %LOCALAPPDATA% are expanded by
# _expand_profile_pattern() instead of here, so this table stays static.
_WINDOWS_GECKO_PROFILE_ROOTS = {
    "firefox": (
        "%APPDATA%\\Mozilla\\Firefox\\Profiles",
        "%APPDATA%\\Mozilla\\Firefox",
    ),
    "librewolf": (
        "%APPDATA%\\librewolf\\Profiles",
        "%LOCALAPPDATA%\\librewolf\\Profiles",
    ),
    "waterfox": (
        "%APPDATA%\\Waterfox\\Profiles",
        "%APPDATA%\\waterfox\\Profiles",
        "%LOCALAPPDATA%\\Waterfox\\Profiles",
    ),
    "zen": (
        "%APPDATA%\\zen\\Profiles",
        "%APPDATA%\\Zen Browser\\Profiles",
        "%LOCALAPPDATA%\\zen\\Profiles",
    ),
    "floorp": (
        "%APPDATA%\\Floorp\\Profiles",
        "%LOCALAPPDATA%\\Floorp\\Profiles",
    ),
}

# Every gecko-family browser key routed through the sessionstore reader.
_GECKO_SESSIONSTORE_BROWSERS = set(_GECKO_PROFILE_ROOTS)


# --- Chromium-family history fallback ----------------------------------------
#
# Chrome/Chromium/Brave/Edge/Opera (and Opera GX) do not expose their UI
# through AT-SPI on some Wayland sessions — the browser frame shows up as a
# stub with no children. They do, however, keep a plain SQLite History
# database that is updated while browsing, so the current page can be found
# there: match the window title (page title) against the most recent
# visited rows. Read-only against a temp copy so the live database lock
# never matters.

# Chromium-family History fallback per browser, across packaging forms
# (deb/rpm direct config, flatpak ~/.var/app, snap ~/snap/<name>). Some
# roots may contain glob chars; each root may either hold the profile's
# History DB directly (Opera) or hold profile subdirectories that do
# (Chrome's Default/, Profile 1/...).

_CHROMIUM_PROFILE_ROOTS = {
    "chrome": (
        "~/.config/google-chrome",
        "~/.var/app/com.google.Chrome/config/google-chrome",
    ),
    "chromium": (
        "~/.config/chromium",
        "~/snap/chromium/common/chromium",
        "~/.var/app/org.chromium.Chromium/config/chromium",
    ),
    "brave": (
        "~/.config/BraveSoftware/Brave-Browser",
        "~/snap/brave/current/.config/BraveSoftware/Brave-Browser",
        "~/.var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser",
    ),
    "edge": (
        "~/.config/microsoft-edge",
        "~/.var/app/com.microsoft.Edge/config/microsoft-edge",
    ),
    "opera": (
        "~/.config/opera",
        "~/.config/opera-gx",
        "~/snap/opera/common/.config/opera",
        "~/.var/app/com.opera.Opera/config/opera",
        "~/.var/app/com.opera.OperaGX/config/opera-gx",
    ),
    "vivaldi": (
        "~/.config/vivaldi",
        "~/.var/app/com.vivaldi.Vivaldi/config/vivaldi",
    ),
}

# Windows user-data directories. As above, the Chromium-family History DB sits
# either directly in one of these (Opera) or in a profile subdirectory
# (Chrome's Default/, Edge's Profile 1/, ...).
_WINDOWS_CHROMIUM_PROFILE_ROOTS = {
    "chrome": (
        "%LOCALAPPDATA%\\Google\\Chrome\\User Data",
    ),
    "chromium": (
        "%LOCALAPPDATA%\\Chromium\\User Data",
    ),
    "edge": (
        "%LOCALAPPDATA%\\Microsoft\\Edge\\User Data",
    ),
    "brave": (
        "%LOCALAPPDATA%\\BraveSoftware\\Brave-Browser\\User Data",
    ),
    "opera": (
        "%APPDATA%\\Opera Software\\Opera Stable",
        "%APPDATA%\\Opera Software\\Opera GX Stable",
    ),
    "vivaldi": (
        "%LOCALAPPDATA%\\Vivaldi\\User Data",
    ),
    "yandex": (
        "%LOCALAPPDATA%\\Yandex\\YandexBrowser\\User Data",
    ),
    "ungoogled_chromium": (
        "%LOCALAPPDATA%\\ungoogled-chromium\\User Data",
    ),
    "iridium": (
        "%LOCALAPPDATA%\\Iridium\\User Data",
    ),
    "comodo_dragon": (
        "%LOCALAPPDATA%\\Comodo\\Dragon\\User Data",
    ),
    "epic_privacy_browser": (
        "%LOCALAPPDATA%\\Epic Privacy Browser\\User Data",
    ),
    "tor_browser": (
        "%APPDATA%\\Tor Browser\\TorBrowser\\Data\\Browser\\profile.default",
    ),
    "cent_browser": (
        "%LOCALAPPDATA%\\CentBrowser\\User Data",
    ),
    "maxthon": (
        "%APPDATA%\\Maxthon\\User Data",
    ),
    "kinza": (
        "%APPDATA%\\KinzaBrowser\\User Data",
    ),
    "coc_coc": (
        "%LOCALAPPDATA%\\CocCoc\\Browser\\User Data",
    ),
    "avast_secure_browser": (
        "%LOCALAPPDATA%\\AVAST\\Avast Secure Browser\\User Data",
    ),
    "ccleaner_browser": (
        "%LOCALAPPDATA%\\Piriform\\CCleaner Browser\\User Data",
    ),
}


def _expand_profile_pattern(pattern):
    """Expand one profile path pattern for this platform.

    "~" is expanded everywhere, exactly as before. On Windows the variables
    Windows configures (%APPDATA%, %LOCALAPPDATA%) are expanded as well; on
    Linux the patterns contain no variables, so the result is unchanged.
    """
    return os.path.expandvars(os.path.expanduser(pattern))


def gecko_profile_roots(browser_key):
    """Sessionstore roots for a Gecko-family browser on this platform."""
    if IS_WINDOWS:
        return tuple(_WINDOWS_GECKO_PROFILE_ROOTS.get(browser_key, ()))
    return _GECKO_PROFILE_ROOTS.get(browser_key, ())


def chromium_profile_roots(browser_key):
    """User-data roots for a Chromium-family browser on this platform."""
    if IS_WINDOWS:
        return tuple(_WINDOWS_CHROMIUM_PROFILE_ROOTS.get(browser_key, ()))
    return _CHROMIUM_PROFILE_ROOTS.get(browser_key, ())

# Profile directories that never contain a user's browsing history.
_PROFILE_DIR_SKIPS = {
    "system profile", "guest profile", "crashpad", "registration",
    "component updater", "crash reports", "pending pings", "profile groups",
}


def _canonical(raw):
    """Reduce an app id to an APP_METADATA key.

    Handles the messy ids desktops hand out: "firefox_firefox" (snap),
    "org.mozilla.firefox" (flatpak), "google-chrome-stable", "vscode"...
    """
    raw = (raw or "").strip().lower()
    if raw in APP_ALIASES:
        return APP_ALIASES[raw]
    if raw in _ALL_APP_KEYS:
        return raw
    # snap/flatpak/deb-style ids: find a token that names a known app
    for token in re.split(r"[-_. ]", raw):
        if not token:
            continue
        if token in APP_ALIASES:
            return APP_ALIASES[token]
        if token in _ALL_APP_KEYS:
            return token
    if IS_WINDOWS:
        # On Windows the identity is the executable name, which often carries
        # a brand prefix or a machine suffix ("msedge", "pycharm64",
        # "applicationframehost", "chrome.exe").
        if raw in _WINDOWS_APP_ALIASES:
            return _WINDOWS_APP_ALIASES[raw]
        for token in re.split(r"[-_. ]", raw):
            if token and token in _WINDOWS_APP_ALIASES:
                return _WINDOWS_APP_ALIASES[token]
    return raw


def _title_suffix_re(suffix):
    """RegExp for a title suffix after ' - ', ' — ' or ' – ' (KDE, Firefox
    and friends use em dashes, most Chromium windows use plain hyphens)."""
    return re.compile(r"\s+[-\u2013\u2014]\s+" + re.escape(suffix) + r"\s*$", re.I)


def _tab_title_from_window(window_title):
    """Strip the browser/identity suffix from a window title.

    Window titles look like "<tab title> - Chromium" or
    "<file path> - <project> - Visual Studio Code"; drop the LAST known
    suffix and return what's left (the active tab's title).
    """
    if not window_title:
        return None
    for suffix, _ in _title_suffix_map():
        m = _title_suffix_re(suffix).search(window_title)
        if m:
            return window_title[: m.start()].strip() or None
    return window_title.strip() or None


def _match_app(app_name, window_title):
    """Map the raw app id / window title to (class, app key)."""
    raw = _canonical(app_name).strip().lower()
    for cls, apps in APP_METADATA.items():
        if raw in apps:
            return cls, raw
    alias = APP_ALIASES.get(raw, raw)
    if alias != raw:
        for cls, apps in APP_METADATA.items():
            if alias in apps:
                return cls, alias
    if IS_WINDOWS:
        alias = _WINDOWS_APP_ALIASES.get(raw, raw)
        if alias != raw:
            for cls, apps in APP_METADATA.items():
                if alias in apps:
                    return cls, alias
    # Some Wine/Lutris games expose a generic process class but put the
    # launcher/game name in the window caption.
    title = (window_title or "").strip().lower()
    for game_key, names in _GAME_DISPLAY_NAMES.items():
        if title in names or any(_title_suffix_re(name).search(title) for name in names):
            return "game", game_key
    # fall back to window-title suffixes (always names a known app)
    for suffix, key in _title_suffix_map():
        if _title_suffix_re(suffix).search(window_title or ""):
            for cls, apps in APP_METADATA.items():
                if key in apps:
                    return cls, key
    if IS_WINDOWS:
        # Windows framework (Store) windows expose no process identity of
        # their own, so the whole title is all there is to go on.
        key = _WINDOWS_TITLE_APPS.get(_windows_title_key(window_title))
        if key:
            for cls, apps in APP_METADATA.items():
                if key in apps:
                    return cls, key
    return None, None


def _windows_title_key(window_title):
    """Normalize a Windows window title for the exact-match title table."""
    title = re.sub(r"\s+", " ", (window_title or "").strip().casefold())
    return title.rstrip("!")


def _match_webapp(domain):
    """Match a site domain to WEBAPP_METADATA; ('youtube_music' etc. handled)."""
    if not domain:
        return None
    # YouTube Music must be checked before the youtube.com suffix match
    if domain == "music.youtube.com" or domain.endswith(".music.youtube.com"):
        return "youtube_music"
    matches = [
        key for key in WEBAPP_METADATA
        if key != "_default" and (domain == key or domain.endswith("." + key))
    ]
    # Prefer the most specific host (e.g. outlook.office.com over office.com).
    return max(matches, key=len) if matches else "_default"


# When the URL isn't available (snap Firefox hides its URL bar behind
# AppArmor), many sites still identify themselves by their title suffix.
_WEBAPP_TITLE_HINTS = (
    (r"\s+[-\u2013\u2014]\s+YouTube Music$", "youtube_music"),
    (r"\s+[-\u2013\u2014]\s+YouTube$", "youtube.com"),
    (r"\s+\|\s+Netflix$", "netflix.com"),
    (r"\s+\|\s+Disney\+$", "disneyplus.com"),
    (r"\s+\|\s+Spotify", "spotify.com"),
    (r"\s+[-\u2013\u2014]\s+Twitch$", "twitch.tv"),
    (r"\s+[-\u2013\u2014]\s+Reddit$", "reddit.com"),
    (r"^Reddit\s+[-\u2013\u2014]\s+", "reddit.com"),
    (r"\s+[-\u2013\u2014]\s+Google Docs$", "docs.google.com"),
    (r"[\s|]+\s*Notion.?$", "notion.so"),
    (r"\s+[-\u2013\u2014]\s+Overleaf, Online LaTeX Editor", "overleaf.com"),
    (r"\s+[-\u2013\u2014|]\s+Gmail$", "mail.google.com"),
    (r"\s+[-\u2013\u2014|]\s+Outlook(?:\s+Web)?$", "outlook.office.com"),
    (r"\s+[-\u2013\u2014|]\s+Yahoo Mail$", "mail.yahoo.com"),
    (r"\s+[-\u2013\u2014|]\s+Proton Mail$", "mail.proton.me"),
    (r"\s+[-\u2013\u2014|]\s+Fastmail$", "app.fastmail.com"),
    (r"\s+[-\u2013\u2014|]\s+Zoho Mail$", "mail.zoho.com"),
    (r"\s+[-\u2013\u2014|]\s+AOL Mail$", "mail.aol.com"),
    (r"\s+[-\u2013\u2014|]\s+ChatGPT$", "chatgpt.com"),
    (r"\s+[-\u2013\u2014|]\s+Claude$", "claude.ai"),
    (r"\s+[-\u2013\u2014|]\s+DeepSeek$", "chat.deepseek.com"),
    (r"\s+[-\u2013\u2014|]\s+Gemini$", "gemini.google.com"),
    (r"\s+[-\u2013\u2014|]\s+Copilot$", "copilot.microsoft.com"),
    (r"\s+[-\u2013\u2014|]\s+Perplexity$", "perplexity.ai"),
    (r"\s+[-\u2013\u2014|]\s+Grok$", "grok.com"),
    (r"\s+[-\u2013\u2014|]\s+Mistral Le Chat$", "chat.mistral.ai"),
    (r"\s+[-\u2013\u2014|]\s+Poe$", "poe.com"),
    (r"\s+[-\u2013\u2014|]\s+Qwen Chat$", "chat.qwen.ai"),
    (r"\s+[-\u2013\u2014|]\s+Meta AI$", "meta.ai"),
    (r"\s+[-\u2013\u2014|]\s+Kimi$", "kimi.com"),
    (r"\s+[-\u2013\u2014|]\s+Z.ai Chat$", "chat.z.ai"),
    (r"\s+[-\u2013\u2014|]\s+You.com$", "you.com"),
    (r"\s+[-\u2013\u2014|]\s+Character\.AI$", "character.ai"),
)


def _match_webapp_from_title(tab_title):
    """Guess the webapp from a known site title suffix; None when unknown."""
    if not tab_title:
        return None
    for pat, key in _WEBAPP_TITLE_HINTS:
        if re.search(pat, tab_title, re.I):
            return key
    return None
