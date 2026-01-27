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
        日次レポートを管理者に送信します（v2.3版、グループごと送信）。
        
        各グループごとに、そのグループのadmin_idsにメンションをつけて
        グループメンバーの勤怠のみを含むレポートを送信します。
        
        Args:
            date_str: 対象日（YYYY-MM-DD形式）
            workspace_id: Slackワークスペースの一意ID（必須）
            
        Note:
            v2.3では、グループごとに個別のレポートメッセージを送信します。
            各メッセージの冒頭にはそのグループのadmin_ids全員分をメンションで付けます。
        """
        if not self.attendance_service:
            logger.error("attendance_service が未設定のためレポート送信不可。")
            return
        
        if not workspace_id:
            logger.error("workspace_id が指定されていないためレポート送信不可。")
            return

        start_time = time.time()
        
        # 1. 送信先チャンネルの決定
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
            logger.warning("送信先チャンネルが見つかりません。")
            return
        
        # 2. 日付タイトルの準備
        try:
            dt = datetime.date.fromisoformat(date_str)
        except:
            dt = datetime.date.today()
            logger.warning(f"日付のパースに失敗したため今日の日付を使用: {dt}")
            
        weekday_list = ["月", "火", "水", "木", "金", "土", "日"]
        month_day = dt.strftime('%m/%d')
        weekday = weekday_list[dt.weekday()]
        
        # 3. グループ情報を取得
        from resources.services.group_service import GroupService
        group_service = GroupService()
        all_groups = group_service.get_all_groups(workspace_id)
        
        if not all_groups:
            logger.warning(f"グループが設定されていません: Workspace={workspace_id}")
            return

        # 4. その日の全勤怠記録を一括取得（効率化）
        from resources.shared.db import get_today_records
        all_today_records = get_today_records(workspace_id, date_str)
        attendance_lookup = {r['user_id']: r for r in all_today_records}

        # 5. 全グループに所属する全メンバーのIDを抽出（名前解決用）
        all_member_ids = set()
        for g in all_groups:
            all_member_ids.update(g.get("member_ids", []))
            all_member_ids.update(g.get("admin_ids", []))
        
        # 6. IDから名前への変換マップを作成
        user_name_map = self.slack_wrapper.fetch_user_name_map(list(all_member_ids))

        # 7. グループごとにレポートを生成・送信
        logger.info("===== レポート送信処理開始（v2.3形式） =====")
        for group in all_groups:
            group_name = group.get("name", "不明なグループ")
            member_ids = group.get("member_ids", [])
            admin_ids = group.get("admin_ids", [])
            
            # 管理者メンション（<@UID>形式でメンションが効くようにする）
            mention_text = " ".join([f"<@{uid}>" for uid in admin_ids]) if admin_ids else ""
            logger.info(f"グループ '{group_name}' のレポート生成: admin_ids={admin_ids}, mention_text={mention_text}")
            
            # レポートブロックの構築
            blocks = []
            
            # 管理者メンション（plain_text形式、<@UID>はSlackが自動でメンションに変換）
            if mention_text:
                blocks.append({
                    "type": "section",
                    "text": {"type": "plain_text", "text": mention_text, "emoji": True}
                })
            
            # タイトル（グループ名を含む）
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*{month_day}({weekday})の勤怠（{group_name}）*"}
            })
            blocks.append({"type": "divider"})
            
            # ステータスごとにグルーピング
            status_map = {}
            for user_id in member_ids:
                if user_id in attendance_lookup:
                    record = attendance_lookup[user_id]
                    st = record.get('status', 'other')
                    display_name = user_name_map.get(user_id, user_id)
                    note = record.get('note', '')
                    
                    if st not in status_map:
                        status_map[st] = []
                    
                    # 備考がある場合はカッコ内に追加
                    if note:
                        status_map[st].append(f"{display_name}（{note}）")
                    else:
                        status_map[st].append(display_name)
            
            # 各ステータスをmrkdwn形式で表示（改行とタブで整形）
            from resources.constants import STATUS_TRANSLATION
            logger.info(f"グループ '{group_name}' のステータスマップ: {status_map}")
            
            # 全休
            if "vacation" in status_map:
                users_text = " \n\t".join(status_map["vacation"])
                blocks.append({
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*全休：* \n\t{users_text}"}
                })
            
            # AM休
            if "vacation_am" in status_map:
                users_text = " \n\t".join(status_map["vacation_am"])
                blocks.append({
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*AM休：* \n\t{users_text}"}
                })
            
            # PM休
            if "vacation_pm" in status_map:
                users_text = " \n\t".join(status_map["vacation_pm"])
                blocks.append({
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*PM休：* \n\t{users_text}"}
                })
            
            # 時間休
            if "vacation_hourly" in status_map:
                users_text = " \n\t".join(status_map["vacation_hourly"])
                blocks.append({
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*時間休：* \n\t{users_text}"}
                })
            
            # 休暇系の後に区切り
            if any(k in status_map for k in ["vacation", "vacation_am", "vacation_pm", "vacation_hourly"]):
                blocks.append({"type": "divider"})
            
            # 電車遅延
            if "late_delay" in status_map:
                users_text = " \n\t".join(status_map["late_delay"])
                blocks.append({
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*電車遅延：* \n\t{users_text}"}
                })
            
            # 遅刻
            if "late" in status_map:
                users_text = " \n\t".join(status_map["late"])
                blocks.append({
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*遅刻：* \n\t{users_text}"}
                })
            
            # 遅刻系の後に区切り
            if any(k in status_map for k in ["late_delay", "late"]):
                blocks.append({"type": "divider"})
            
            # 在宅
            if "remote" in status_map:
                users_text = " \n\t".join(status_map["remote"])
                blocks.append({
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*在宅：* \n\t{users_text}"}
                })
            
            # 在宅の後に区切り
            if "remote" in status_map:
                blocks.append({"type": "divider"})
            
            # 外出
            if "out" in status_map:
                users_text = " \n\t".join(status_map["out"])
                blocks.append({
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*外出：* \n\t{users_text}"}
                })
            
            # 外出の後に区切り
            if "out" in status_map:
                blocks.append({"type": "divider"})
            
            # シフト勤務
            if "shift" in status_map:
                users_text = " \n\t".join(status_map["shift"])
                blocks.append({
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*シフト勤務：* \n\t{users_text}"}
                })
                blocks.append({"type": "divider"})
            
            # 早退
            if "early_leave" in status_map:
                users_text = " \n\t".join(status_map["early_leave"])
                blocks.append({
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*早退：* \n\t{users_text}"}
                })
                blocks.append({"type": "divider"})
            
            # その他
            if "other" in status_map:
                users_text = " \n\t".join(status_map["other"])
                blocks.append({
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*その他：* \n\t{users_text}"}
                })
                blocks.append({"type": "divider"})
            
            # 該当者がいない場合
            if not status_map:
                blocks.append({
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "_勤怠連絡はありません_"}
                })

            # 8. メッセージ送信
            try:
                for channel_id in target_channels:
                    self.slack_wrapper.send_message(
                        channel=channel_id,
                        blocks=blocks,
                        text=f"{group_name}の{month_day}({weekday})の勤怠"
                    )
                    logger.info(f"レポート送信成功: Group={group_name}, Channel={channel_id}")
            except Exception as e:
                logger.error(f"グループレポート送信エラー: Group={group_name}, {e}")
        
        total_end = time.time()
        logger.info(f"レポート送信処理完了 所要時間: {total_end - start_time:.4f}秒")

    # ==========================================
    # 後方互換性のため（旧メソッド名）
    # ==========================================
    def _get_display_name(self, user_id: str) -> str:
        """旧メソッド名との互換性のため（非推奨）"""
        return self.fetch_user_display_name(user_id)
