"""
通知サービス

このモジュールは、Slackへのメッセージ送信・通知処理を担当します。
勤怠カード、日次レポート、エラーメッセージなどの送信を統括します。

命名規則:
- fetch_xxx: データ取得
- notify_xxx: 通知送信
- execute_xxx: 実行処理
"""

import datetime
import logging
import time
import os
from typing import Any, Dict, List, Optional

# ステータス翻訳をインポート
from resources.constants import STATUS_TRANSLATION
from resources.clients.slack_client import SlackClientWrapper
from resources.templates.cards import build_attendance_card, build_delete_notification

logger = logging.getLogger(__name__)


class NotificationService:
    """
    Slack通知を管理するサービスクラス。
    
    勤怠記録の通知、日次レポートの配信、エラーメッセージの送信などを提供します。
    """

    def __init__(self, slack_client, attendance_service=None):
        """
        通知サービスの初期化。
        
        Args:
            slack_client: Slack Web API クライアント（slack_sdk.web.client.WebClient）
            attendance_service: AttendanceServiceインスタンス（レポート生成に使用）
        """
        self.client = slack_client
        self.slack_wrapper = SlackClientWrapper(slack_client)
        self.attendance_service = attendance_service

    # ==========================================
    # 名前解決（内部メソッド）
    # ==========================================
    def fetch_user_display_name(self, user_id: str) -> str:
        """
        ユーザーIDから表示名を取得します。
        
        Args:
            user_id: SlackユーザーID
            
        Returns:
            表示名（優先順位: display_name > real_name > user_id）
        """
        return self.slack_wrapper.fetch_user_display_name(user_id)

    # ==========================================
    # 勤怠通知（統一インターフェース）
    # ==========================================
    def notify_attendance_change(
        self, 
        record: Any, 
        channel: str,
        thread_ts: Optional[str] = None,
        is_update: bool = False,
        is_delete: bool = False
    ) -> None:
        """
        勤怠記録の変更をSlackに通知します。
        
        Args:
            record: AttendanceRecordオブジェクトまたは辞書
            channel: 投稿先チャンネルID
            thread_ts: スレッドのタイムスタンプ（スレッド返信する場合）
            is_update: 更新通知かどうか
            is_delete: 削除通知かどうか
            
        Note:
            このメソッドが唯一の通知送信の入口となります。
            内部で必ずfetch_user_display_nameを呼び出し、
            整形済みの名前をView層（build_attendance_card）に渡します。
        """
        try:
            # ユーザーIDを取得
            user_id = record.user_id if hasattr(record, 'user_id') else record.get('user_id')
            
            # 【重要】名前を必ず解決してから View層に渡す
            display_name = self.fetch_user_display_name(user_id)
            
            # 1. 削除通知の場合
            if is_delete:
                date_val = record.date if hasattr(record, 'date') else record.get('date')
                blocks = build_delete_notification(display_name, date_val)
                
                self.slack_wrapper.send_message(
                    channel=channel,
                    blocks=blocks,
                    text=f"勤怠連絡を取り消しました: {date_val}",
                    thread_ts=thread_ts
                )
                logger.info(f"削除通知を送信しました: User={user_id}, Date={date_val}")
                return

            # 2. 記録・更新通知の場合
            blocks = build_attendance_card(
                record=record,
                display_name=display_name,  # 整形済みの名前を渡す
                is_update=is_update,
                show_buttons=True
            )
            
            date_val = record.date if hasattr(record, 'date') else record.get('date')
            text = "勤怠記録を更新しました" if is_update else "勤怠を記録しました"
            
            self.slack_wrapper.send_message(
                channel=channel,
                blocks=blocks,
                text=text,
                thread_ts=thread_ts
            )
            
            logger.info(f"勤怠カードを送信しました: User={user_id}, Date={date_val}, Update={is_update}")
            
        except Exception as e:
            logger.error(f"通知送信失敗: {e}", exc_info=True)

    # ==========================================
    # 日次レポート送信
    # ==========================================
    def send_daily_report(self, date_str: str, workspace_id: str = None) -> None: 
        """
        日次レポートを管理者に送信します（v2.0版、マルチテナント対応）。
        
        Args:
            date_str: 対象日（YYYY-MM-DD形式）
            workspace_id: Slackワークスペースの一意ID（必須）
            
        Note:
            v2.0では、グループ構造と管理者設定を使用してレポートを生成・送信します。
            マルチテナント対応により、環境変数ではなくFirestoreから設定を取得します。
        """
        if not self.attendance_service:
            logger.error("attendance_service が未設定のためレポート送信不可。")
            return
        
        if not workspace_id:
            logger.error("workspace_id が指定されていないためレポート送信不可。")
            return

        start_time = time.time()
        
        # 1. 管理者と送信先チャンネルの取得
        from resources.services.workspace_service import WorkspaceService
        workspace_service = WorkspaceService()
        admin_ids = workspace_service.get_admin_ids(workspace_id)

        mention_text = " ".join([f"<@{uid}>" for uid in admin_ids]) if admin_ids else ""
        
        if not admin_ids:
            logger.warning(f"管理者が設定されていません: Workspace={workspace_id}")
            return
        
        # 2. 送信先チャンネルの決定
        # マルチテナント対応: workspaces コレクションから report_channel_id を取得
        from resources.shared.db import get_workspace_config
        workspace_config = get_workspace_config(workspace_id)
        
        report_channel_id = None
        if workspace_config:
            report_channel_id = workspace_config.get("report_channel_id")
        
        # report_channel_id が設定されていない場合は Bot が参加しているチャンネルに送信
        if report_channel_id:
            target_channels = [report_channel_id]
        else:
            target_channels = self.slack_wrapper.fetch_bot_joined_channels()
        
        if not target_channels:
            logger.warning("送信先チャンネルが見つからないため、管理者DMへ送信します。")
        
        # 3. 日付タイトルの準備
        try:
            dt = datetime.date.fromisoformat(date_str)
        except:
            dt = datetime.date.today()
            logger.warning(f"日付のパースに失敗したため今日の日付を使用: {dt}")
            
        weekday_list = ["月", "火", "水", "木", "金", "土", "日"]
        title_date = f"{dt.strftime('%m/%d')}({weekday_list[dt.weekday()]})"
        
        # 4. グループ情報を取得
        from resources.services.group_service import GroupService
        group_service = GroupService()
        all_groups = group_service.get_all_groups(workspace_id)
        
        if not all_groups:
            logger.warning(f"グループが設定されていません: Workspace={workspace_id}")
            return

        # 5. 全グループに所属する全メンバーのIDを抽出
        all_member_ids = set()
        for g in all_groups:
            all_member_ids.update(g.get("member_ids", []))
        
        # 6. IDから名前への変換マップを作成
        user_name_map = self.slack_wrapper.fetch_user_name_map(list(all_member_ids))

        # 7. レポートブロックの構築
        blocks = []
        if mention_text:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": mention_text}
            })
        
        blocks.append({
            "type": "header", 
            "text": {"type": "plain_text", "text": f"{title_date}の勤怠一覧"}
        })

        for group in all_groups:
            group_name = group.get("name", "不明なグループ")
            member_ids = group.get("member_ids", [])
            
            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section", 
                "text": {"type": "mrkdwn", "text": f"*{group_name}*"}
            })
            
            if not member_ids:
                blocks.append({
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "_メンバーが登録されていません_"}
                })
                continue

            # 区分ごとにデータを集計
            status_map = {}
            for user_id in member_ids:
                record = self.attendance_service.get_specific_date_record(
                    workspace_id, user_id, date_str
                )
                if record:
                    st = record.get('status', 'other')
                    display_name = user_name_map.get(user_id, user_id)
                    note = f" ({record['note']})" if record.get('note') else ""
                    
                    if st not in status_map:
                        status_map[st] = []
                    status_map[st].append(f"{display_name}{note}")

            if not status_map:
                blocks.append({
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "_勤怠連絡はありません_"}
                })
            else:
                from resources.constants import STATUS_ORDER
                
                # 大分類ごとにセクションを構築
                # 1. 休暇
                vacation_statuses = ["vacation", "vacation_am", "vacation_pm", "vacation_hourly"]
                vacation_lines = []
                for st_key in vacation_statuses:
                    if st_key in status_map:
                        st_label = STATUS_TRANSLATION.get(st_key, st_key)
                        names = ", ".join(status_map[st_key])
                        vacation_lines.append(f"{st_label}：{names}")
                
                if vacation_lines:
                    blocks.append({
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": "休暇\n" + "\n".join(vacation_lines)}
                    })
                
                # 2. 遅刻
                late_statuses = ["late_delay", "late"]
                late_lines = []
                for st_key in late_statuses:
                    if st_key in status_map:
                        st_label = STATUS_TRANSLATION.get(st_key, st_key)
                        names = ", ".join(status_map[st_key])
                        late_lines.append(f"{st_label}：{names}")
                
                if late_lines:
                    blocks.append({
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": "遅刻\n" + "\n".join(late_lines)}
                    })
                
                # 3. その他（在宅、外出、シフト勤務）
                other_statuses = ["remote", "out", "shift"]
                for st_key in other_statuses:
                    if st_key in status_map:
                        st_label = STATUS_TRANSLATION.get(st_key, st_key)
                        names = ", ".join(status_map[st_key])
                        blocks.append({
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": f"{st_label}：{names}"}
                        })
                
                # 4. 早退・その他（あれば表示）
                remaining_statuses = ["early_leave", "other"]
                for st_key in remaining_statuses:
                    if st_key in status_map:
                        st_label = STATUS_TRANSLATION.get(st_key, st_key)
                        names = ", ".join(status_map[st_key])
                        blocks.append({
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": f"{st_label}：{names}"}
                        })

        # 8. メッセージ送信
        try:
            if target_channels:
                for channel_id in target_channels:
                    self.slack_wrapper.send_message(
                        channel=channel_id,
                        blocks=blocks,
                        text=f"{title_date}の勤怠一覧"
                    )
                    logger.info(f"レポート送信成功: {channel_id}")
            else:
                # どこにも参加していない場合は管理者にDM
                for admin_id in admin_ids:
                    dm = self.client.conversations_open(users=admin_id)
                    self.slack_wrapper.send_message(
                        channel=dm["channel"]["id"],
                        blocks=blocks,
                        text=f"{title_date}の勤怠一覧"
                    )

        except Exception as e:
            logger.error(f"送信エラー: {e}")
        
        total_end = time.time()
        logger.info(f"レポート送信処理完了 所要時間: {total_end - start_time:.4f}秒")

    # ==========================================
    # 後方互換性のため（旧メソッド名）
    # ==========================================
    def _get_display_name(self, user_id: str) -> str:
        """旧メソッド名との互換性のため（非推奨）"""
        return self.fetch_user_display_name(user_id)
