# 勤怠取消の拡張対応（問題3修正）2026-02-20

## 問題
勤怠連絡の取消はメッセージに「取消」「キャンセル」「取り消し」「削除」のいずれかを含めないと動作しなかった。

## 追加対応した取消パターン

| パターン | 条件 | 動作 |
|---------|------|------|
| 「間に合った」「間に合いました」 | スレッド返信（遅刻連絡への返信等） | 取消として扱う |
| 「出社した」「出社しました」 | スレッド返信 | 取消として扱う |
| 「出社した」「出社しました」「間に合った」等 | スタンドアロン（スレッド外）かつ**9時前** | 取消として扱う（AIスキップ・今日の記録を削除） |

---

## 変更内容

### Fix 1: スレッド返信ガードの修正

**ファイル:** `resources/listeners/attendance_listener.py`

**変更前の問題:**
スレッド返信で「間に合った」などを含むメッセージが届いた際、AIが `action='delete'` を返しても
`status='other'` になることがあり、以下の条件が通過できなかった:

```python
pattern_b = (
    reply_has_late_cancellation_phrases(text)
    and att_status in ("late", "late_delay")  # ← 過剰条件
)
```

**修正後:**
```python
pattern_b = reply_has_late_cancellation_phrases(text)
```

`att_status` の絞り込みを削除し、「間に合った」「出社した」等のフレーズが含まれていれば
スレッド返信でも取消を許可するようにした。

エラーメッセージも更新:
```
「取消する場合は、メッセージに「取消」「キャンセル」「取り消し」「削除」「間に合った」「出社した」のいずれかを含めて送信してください。」
```

---

### Fix 2: スタンドアロン早朝取消の新規対応

**対象:** スレッド外で「出社した」「間に合った」等を9時前に投稿した場合

**ファイル1:** `resources/services/nlp_service.py`

新関数 `is_early_morning_arrival(text, message_ts)` を追加:
- `LATE_CANCELLATION_PHRASES`（「間に合った」「間に合いました」「出社した」「出社しました」）のいずれかを含む
- かつメッセージ送信時刻が午前9時より前

両条件が揃った場合に `True` を返す。

**ファイル2:** `resources/listeners/attendance_listener.py`

`_handle_message_async` メソッド内、AI呼び出し前に早朝チェックを挿入:
- スタンドアロン（スレッド外）かつ `is_early_morning_arrival()` が True の場合
- AIをスキップして今日の勤怠記録を直接削除
- 削除通知をSlackに送信
- `return` で以降の処理をスキップ

---

## T09R8SWTW49（TTBワークスペース）への影響

**変更なし。** `resources/shared/db.py` の `_get_attendance_collection(workspace_id)` が
すでにワークスペース別コレクション対応済みのため、T09R8SWTW49 は引き続き
`attendance_TTB` コレクションを正しく参照する。

削除フロー:
```
delete_attendance(team_id, user_id, date)
  → delete_attendance_record_db(workspace_id, user_id, date)
    → _get_attendance_collection(workspace_id)  # T09R8SWTW49なら"attendance_TTB"
```

---

## 動作確認ポイント

1. スレッド返信で「間に合いました」→ 削除通知が届くこと（「取消キーワードを含めて」エラーが出ない）
2. スレッド返信で「出社しました」→ 同上
3. スタンドアロンで「出社しました」を8:50に投稿 → 今日の記録が削除され通知が届くこと
4. スタンドアロンで「出社しました」を9:05に投稿 → 通常のAI処理に流れること（取消されない）
5. T09R8SWTW49 ワークスペースで上記ケースが正常動作すること
