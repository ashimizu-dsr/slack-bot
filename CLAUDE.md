# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Slack勤怠管理Bot — a multi-tenant Slack attendance management bot that uses AI (OpenAI GPT-4o-mini) to parse natural language attendance messages in Japanese and stores structured records in Firestore. Built with Slack Bolt for Python, deployed on Google Cloud Run.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run locally (uses Google Cloud Functions Framework)
functions-framework --target=slack_bot --source=resources/main.py --debug

# Deploy to Cloud Run (Windows)
.\deploy.ps1

# Deploy to Cloud Run (Linux/Mac)
./deploy.sh

# Manual deploy via gcloud
gcloud run deploy slack-attendance-bot --source . --region asia-northeast1 --platform managed --allow-unauthenticated
```

There are no automated tests in this project. Testing is done manually against Slack.

## Architecture

### Entry Point & Request Routing

`resources/main.py` is the single entry point (`slack_bot` function). It routes requests by URL path:
- `/slack/events` — Standard Slack events (messages, button clicks, shortcuts), handled by `SlackRequestHandler`
- `/slack/oauth_redirect` — OAuth callback for multi-workspace installation
- `/job/report` — Cloud Scheduler endpoint for daily reports (iterates all workspaces)
- `/pubsub/interactions` — Pub/Sub push endpoint for async processing

### Multi-Tenant Design

Every operation is scoped by `team_id` (Slack workspace ID):
- `get_slack_client(team_id)` in `resources/clients/slack_client.py` dynamically creates a `WebClient` by fetching the bot token from Firestore's `workspaces` collection
- `FirestoreInstallationStore` (defined in `main.py`) implements Slack Bolt's `InstallationStore` for OAuth
- All Firestore document IDs include workspace_id (e.g., `{workspace_id}_{user_id}_{date}` for attendance)

### Environment-Based Collection Names

`resources/constants.py:get_collection_name()` appends `_dev` to Firestore collection names when `APP_ENV != "production"`. The Firestore `database` parameter is also set to `APP_ENV` value.

### Listener Pattern

Three listener classes in `resources/listeners/`, all registered in `__init__.py:register_all_listeners()`:
- **AttendanceListener** — message events, edit/delete button actions, history modal
- **SystemListener** — `member_joined_channel` (triggers channel history backfill)
- **AdminListener** — group management, report settings modals (shortcuts: `open_history_modal`, `open_member_setup_modal`)

Each listener has `handle_sync(app)` for direct Slack Bolt registration and `handle_async(team_id, event)` for Pub/Sub dispatch.

### NLP Pipeline

`resources/services/nlp_service.py:extract_attendance_from_text()` sends messages to OpenAI with extensive few-shot examples in the system prompt. Key behaviors:
- Strikethrough text (`~text~`) is converted to `(strike-through: text)` before sending to AI
- Supports multi-day records via `_additional_attendances` field
- Status normalization uses `STATUS_AI_ALIASES` from `constants.py`
- `action: "delete"` means return to normal work (e.g., plain "出社")

### UI Layer

- `resources/templates/cards.py` — Slack Block Kit message cards (attendance confirmation, report)
- `resources/templates/modals.py` — Slack modal views (history, edit, group management)

### Firestore Collections

Primary collections (suffixed with `_dev` in non-production):
- `workspaces/{team_id}` — OAuth tokens and workspace config
- `attendance/{workspace_id}_{user_id}_{date}` — Individual attendance records
- `groups/{workspace_id}/groups/{group_id}` — Team/department groups with member lists
- `workspace_settings/{workspace_id}` — Admin user IDs, report channel
- `channel_history_processed/{workspace_id}_{channel_id}` — Backfill tracking

## Key Conventions

- All code comments, docstrings, and log messages are in Japanese
- Status values are English keys mapped to Japanese labels via `STATUS_TRANSLATION` in `constants.py`
- The project uses `python-dotenv` for local env loading; in Cloud Run, env vars are set via deploy scripts
- `APP_ENV` controls both Firestore database selection and collection name suffixing
