"""
勤怠ステータスの唯一の定義

全てのステータス情報（翻訳、AIエイリアス、表示順、有効キー）は
このモジュールの STATUSES リストから自動生成されます。
新しいステータスを追加する場合は STATUSES に1行追加するだけで完了します。
"""

from dataclasses import dataclass, field
from typing import Dict, List, Set


@dataclass(frozen=True)
class StatusDef:
    """1つの勤怠ステータスの完全な定義"""
    key: str
    label: str
    ai_aliases: tuple
    display_order: int
    category: str


# ===== 唯一の定義（ここだけを編集すれば全箇所に反映される） =====
STATUSES: List[StatusDef] = [
    # 大分類：休暇
    StatusDef("vacation",        "全休",       ("vacation", "休暇", "休み", "欠勤", "有給", "お休み", "全休"), 10, "vacation"),
    StatusDef("vacation_am",     "AM休",       ("vacation_am", "am休", "午前休", "午前半休", "午前", "AM休"), 11, "vacation"),
    StatusDef("vacation_pm",     "PM休",       ("vacation_pm", "pm休", "午後休", "午後半休", "午後", "PM休"), 12, "vacation"),
    StatusDef("vacation_hourly", "時間休",     ("vacation_hourly", "時間休"), 13, "vacation"),
    # 大分類：遅刻
    StatusDef("late_delay",      "電車遅延",   ("late_delay", "電車遅延", "遅延"), 20, "late"),
    StatusDef("late",            "遅刻",       ("late", "遅刻"), 21, "late"),
    # 大分類：勤務
    StatusDef("remote",          "在宅",       ("remote", "在宅", "リモート", "テレワーク", "在宅勤務"), 30, "work"),
    StatusDef("out",             "外出",       ("out", "外出", "直行", "直帰", "情報センター", "楽天損保"), 31, "work"),
    StatusDef("shift",           "シフト勤務", ("shift", "シフト", "交代勤務", "シフト勤務"), 32, "work"),
    # 大分類：その他
    StatusDef("early_leave",     "早退",       ("early_leave", "早退", "退勤"), 40, "other"),
    StatusDef("other",           "その他",     ("other", "未分類", "その他"), 99, "other"),
]

# ===== 以下は STATUSES から自動生成（手動管理不要） =====

STATUS_TRANSLATION: Dict[str, str] = {s.key: s.label for s in STATUSES}

STATUS_AI_ALIASES: Dict[str, tuple] = {s.key: s.ai_aliases for s in STATUSES}

STATUS_ORDER: List[str] = [s.key for s in sorted(STATUSES, key=lambda s: s.display_order)]

VALID_STATUS_KEYS: Set[str] = {s.key for s in STATUSES}

# レポート表示用: (key, label) のタプルリスト
STATUS_ORDER_WITH_LABELS: List[tuple] = [
    (s.key, s.label) for s in sorted(STATUSES, key=lambda s: s.display_order)
]

# レポート区分ごとの区切り位置（この区分の後にdividerを入れる）
REPORT_DIVIDER_AFTER: Set[str] = {"vacation_hourly", "late", "remote", "out", "shift", "early_leave", "other"}


def normalize_status(value: str) -> str:
    """AIが返したステータス値を正規キーに変換する"""
    val = value.lower().strip()
    for s in STATUSES:
        if val == s.key:
            return s.key
    for s in STATUSES:
        if val in s.ai_aliases:
            return s.key
    return "other"
