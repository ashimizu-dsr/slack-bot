# SOLID原則に基づくリファクタリング設計書

**作成日**: 2026-02-16
**アプローチ**: C — レイヤードアーキテクチャ型（一括リファクタリング）
**目的**: 保守性向上（新機能追加・バグ修正時の変更箇所を最小化）

---

## 1. ディレクトリ構成

### Before

```
resources/
├── clients/slack_client.py
├── handlers/oauth_handler.py
├── listeners/{attendance,admin,system}_listener.py, Listener.py
├── services/{attendance,nlp,notification,report,group,workspace}_service.py
├── shared/db.py(473行), utils.py, errors.py, setup_logger.py
├── templates/cards.py, modals.py
├── constants.py
└── main.py(516行)
```

### After

```
resources/
├── clients/
│   └── slack_client.py              # 変更なし
├── repositories/                    # 【新設】Firestore操作を集約
│   ├── __init__.py
│   ├── base.py                      # BaseRepository（Firestoreクライアント注入）
│   ├── attendance_repository.py     # db.pyの勤怠関連を移行
│   ├── workspace_repository.py      # db.pyのWS関連 + WorkspaceServiceのDB操作を移行
│   └── group_repository.py          # GroupServiceのDB操作を移行
├── listeners/
│   ├── __init__.py                  # register_all_listeners（DI対応に更新）
│   ├── Listener.py                  # 変更なし
│   ├── attendance_listener.py       # 軽量化（NLP+通知の混在を解消）
│   ├── admin_listener.py            # DI化（GroupService直接生成6箇所を排除）
│   └── system_listener.py           # 変更なし
├── services/
│   ├── attendance_service.py        # Repository注入に変更
│   ├── nlp_service.py               # クラス化 + プロンプト定数分離
│   ├── report_service.py            # レポート生成ロジックを統合（唯一のレポート生成場所）
│   ├── group_service.py             # スリム化（9メソッド→5メソッド）
│   └── workspace_service.py         # Repository注入に変更
├── templates/
│   ├── cards.py                     # 変更なし
│   └── modals.py                    # 変更なし
├── shared/
│   ├── errors.py                    # 変更なし
│   ├── setup_logger.py              # 変更なし
│   └── utils.py                     # 変更なし
├── models/                          # 【新設】ステータス定義・型の一元管理
│   ├── __init__.py
│   └── status.py                    # STATUSES定義 + 自動生成された辞書群
├── routes/                          # 【新設】main.pyから分離したルーティング
│   ├── __init__.py
│   ├── job_routes.py                # /job/report ハンドラ
│   └── pubsub_routes.py             # /pubsub/interactions ハンドラ
├── auth/                            # 【新設】OAuth関連
│   ├── __init__.py
│   └── installation_store.py        # FirestoreInstallationStore
├── constants.py                     # 環境変数のみ（ステータス定義はmodels/に移動）
└── main.py                          # 初期化+ルーティング委譲のみ（約60行）
```

### 廃止するファイル

- `shared/db.py` → `repositories/` に分割移行
- `services/notification_service.py` → `report_service.py` に統合、通知送信はリスナーから直接
- `handlers/oauth_handler.py` → `auth/installation_store.py` に置き換え
- `listeners/attendance_listener_new.py` → 使用されていない旧ファイル

---

## 2. ステータス一元化（OCP修正）

### 問題

ステータス定義が5箇所に散在。新ステータス追加に4+ファイル変更が必要。

- `constants.py`: `STATUS_TRANSLATION`, `STATUS_AI_ALIASES`, `STATUS_ORDER`
- `nlp_service.py`: `STATUS_ALIASES`（重複定義）
- `notification_service.py`: `status_order`（ハードコード）
- `admin_listener.py`: `status_order`（コピペ）
- `attendance_service.py`: `self.valid_statuses`（ハードコード）

### 解決

`models/status.py` に `StatusDef` データクラスを定義し、`STATUSES` リストを唯一の情報源とする。

```python
@dataclass(frozen=True)
class StatusDef:
    key: str                    # 正規キー（例: "vacation_am"）
    label: str                  # 日本語表示名（例: "AM休"）
    ai_aliases: List[str]       # AIが返す可能性のある表現
    display_order: int          # レポート表示順
    category: str               # 大分類（"vacation", "late", "work", "other"）

STATUSES = [
    StatusDef("vacation",         "全休",       ["vacation", "休暇", "休み", "欠勤", "有給", "お休み", "全休"], 10, "vacation"),
    StatusDef("vacation_am",      "AM休",       ["vacation_am", "am休", "午前休", "午前半休", "午前", "AM休"], 11, "vacation"),
    StatusDef("vacation_pm",      "PM休",       ["vacation_pm", "pm休", "午後休", "午後半休", "午後", "PM休"], 12, "vacation"),
    StatusDef("vacation_hourly",  "時間休",     ["vacation_hourly", "時間休"], 13, "vacation"),
    StatusDef("late_delay",       "電車遅延",   ["late_delay", "電車遅延", "遅延"], 20, "late"),
    StatusDef("late",             "遅刻",       ["late", "遅刻"], 21, "late"),
    StatusDef("remote",           "在宅",       ["remote", "在宅", "リモート", "テレワーク", "在宅勤務"], 30, "work"),
    StatusDef("out",              "外出",       ["out", "外出", "直行", "直帰", "情報センター", "楽天損保"], 31, "work"),
    StatusDef("shift",            "シフト勤務", ["shift", "シフト", "交代勤務", "シフト勤務"], 32, "work"),
    StatusDef("early_leave",      "早退",       ["early_leave", "早退", "退勤"], 40, "other"),
    StatusDef("other",            "その他",     ["other", "未分類", "その他"], 99, "other"),
]

# 自動生成（手動管理不要）
STATUS_TRANSLATION = {s.key: s.label for s in STATUSES}
STATUS_AI_ALIASES = {s.key: s.ai_aliases for s in STATUSES}
STATUS_ORDER = [s.key for s in sorted(STATUSES, key=lambda s: s.display_order)]
VALID_STATUS_KEYS = {s.key for s in STATUSES}

def normalize_status(value: str) -> str:
    val = value.lower().strip()
    for s in STATUSES:
        if val == s.key or val in s.ai_aliases:
            return s.key
    return "other"
```

**効果**: 新ステータス追加 = `STATUSES` に1行追加するだけ。他ファイル変更不要。

---

## 3. Repository層（DIP修正）

### 問題

- `db.py`(473行) がモジュールレベルで `firestore.Client` を直接生成
- `GroupService`, `WorkspaceService` が各自で `firestore.Client()` を生成
- `AdminListener` が `firestore.Client()` を直接生成してGroupServiceを迂回

### 解決

#### BaseRepository

```python
class BaseRepository:
    def __init__(self, db: firestore.Client):
        self._db = db

    def _collection(self, base_name: str):
        return self._db.collection(get_collection_name(base_name))
```

#### 移行マッピング

| db.py の関数 | 移行先 |
|---|---|
| `save_attendance_record` | `AttendanceRepository.save` |
| `get_single_attendance_record` | `AttendanceRepository.get_single` |
| `get_user_history_from_db` | `AttendanceRepository.get_history` |
| `delete_attendance_record_db` | `AttendanceRepository.delete` |
| `get_today_records` | `AttendanceRepository.get_by_date` |
| `get_attendance_records_by_sections` | `AttendanceRepository.get_by_sections` |
| `get_channel_members_with_section` | `AttendanceRepository.get_section_config` |
| `save_channel_members_db` | `AttendanceRepository.save_section_config` |
| `get_workspace_config` | `WorkspaceRepository.get_config` |
| `save_workspace_config` | `WorkspaceRepository.save_config` |
| `is_channel_history_processed` | `WorkspaceRepository.is_channel_processed` |
| `mark_channel_history_processed` | `WorkspaceRepository.mark_channel_processed` |
| `init_db()` | 廃止（main.pyの初期化に吸収）|

#### GroupRepository

`GroupService` の Firestore 操作部分を抽出:

```python
class GroupRepository(BaseRepository):
    def create(self, workspace_id, group_id, data): ...
    def get_all(self, workspace_id): ...
    def get_by_id(self, workspace_id, group_id): ...
    def update(self, workspace_id, group_id, data): ...
    def delete(self, workspace_id, group_id): ...
```

---

## 4. Service層の責務分離（SRP修正）

### 4-1. レポート生成の統合

**問題**: `NotificationService.send_daily_report` と `AdminListener._generate_debug_report` にほぼ同一のレポート生成ロジックが重複。

**解決**: `ReportService` に統合。

```python
class ReportService:
    def __init__(self, attendance_repo, group_repo, workspace_repo):
        self._attendance_repo = attendance_repo
        self._group_repo = group_repo
        self._workspace_repo = workspace_repo

    def build_daily_report_blocks(self, workspace_id, date_str, user_name_map):
        """レポートのBlock Kitブロックを生成して返す（送信はしない）"""
        ...
```

`NotificationService` は廃止:
- レポート生成 → `ReportService.build_daily_report_blocks`
- レポート送信 → `job_routes.py` から `client.chat_postMessage` で直接送信
- 勤怠変更通知 → `AttendanceListener` 内で `cards.py` を使って直接送信

### 4-2. GroupService のスリム化（ISP修正）

9メソッド → 5メソッドに削減:

```python
class GroupService:
    def __init__(self, group_repo):
        self._repo = group_repo

    def create(self, workspace_id, name, member_ids, admin_ids=None): ...
    def get_all(self, workspace_id): ...
    def get_by_id(self, workspace_id, group_id): ...
    def update(self, workspace_id, group_id, **fields): ...
    def delete(self, workspace_id, group_id): ...
```

UUID方式メソッド3個と個別フィールド更新メソッド3個を廃止。`update` が可変キーワード引数で任意フィールドを更新。

### 4-3. NlpService のクラス化

```python
_SYSTEM_INSTRUCTION = "..."  # 関数外に定数として定義
_FEW_SHOT_EXAMPLES = [...]   # 関数外に定数として定義

class NlpService:
    def __init__(self, api_key: str):
        self._client = OpenAI(api_key=api_key) if OpenAI and api_key else None

    def extract(self, text, base_date, team_id=None, user_id=None):
        """テキストから勤怠情報を抽出"""
        ...
```

- `_normalize_status` → `models/status.py:normalize_status` に移動
- プロンプト定義をモジュールレベル定数に分離
- OpenAIクライアントをコンストラクタで受け取り（テスト時モック可能）

---

## 5. main.py 分割と DI

### main.py（約60行に縮小）

```python
# 初期化: 依存関係の組み立て
db_client = firestore.Client(database=os.getenv("APP_ENV"))

attendance_repo = AttendanceRepository(db_client)
workspace_repo = WorkspaceRepository(db_client)
group_repo = GroupRepository(db_client)

nlp_service = NlpService(api_key=os.getenv("OPENAI_API_KEY"))
attendance_service = AttendanceService(attendance_repo, nlp_service)
group_service = GroupService(group_repo)
report_service = ReportService(attendance_repo, group_repo, workspace_repo)

attendance_listener = AttendanceListener(attendance_service)
system_listener = SystemListener(attendance_service)
admin_listener = AdminListener(group_service, report_service)

# エントリポイント
def slack_bot(request):
    path = request.path
    if path == "/slack/oauth_redirect":
        return slack_handler.handle(request)
    if path == "/job/report":
        return handle_report_job(request, report_service, workspace_repo, get_slack_client)
    if path == "/pubsub/interactions":
        return handle_pubsub(request, listener_map)
    return slack_handler.handle(request)
```

### ルーティングの分離

- `routes/job_routes.py`: `/job/report` のハンドラ（main.py L430-477 を移行）
- `routes/pubsub_routes.py`: `/pubsub/interactions` のハンドラ（main.py L480-509 を移行）

### OAuth の分離

- `auth/installation_store.py`: `FirestoreInstallationStore`（main.py L82-183 を移行）

### Listener コンストラクタの統一（LSP修正）

```python
class AttendanceListener(Listener):
    def __init__(self, attendance_service): ...

class SystemListener(Listener):
    def __init__(self, attendance_service): ...

class AdminListener(Listener):
    def __init__(self, group_service, report_service): ...

def register_all_listeners(app, attendance_service, group_service, report_service):
    ...
```

---

## 6. 依存関係の全体像

```
main.py (初期化 + ルーティング委譲)
  │
  ├── routes/ (HTTPリクエストハンドラ)
  │     ├── job_routes.py     → ReportService + SlackClient
  │     └── pubsub_routes.py  → listener_map
  │
  ├── listeners/ (Slackイベント受信)
  │     ├── AttendanceListener → AttendanceService
  │     ├── AdminListener      → GroupService + ReportService
  │     └── SystemListener     → AttendanceService
  │
  ├── services/ (ビジネスロジック)
  │     ├── AttendanceService  → AttendanceRepository + NlpService
  │     ├── ReportService      → AttendanceRepository + GroupRepository + WorkspaceRepository
  │     ├── GroupService       → GroupRepository
  │     └── NlpService         → OpenAI (注入)
  │
  ├── repositories/ (データアクセス)
  │     ├── AttendanceRepository → firestore.Client (注入)
  │     ├── GroupRepository      → firestore.Client (注入)
  │     └── WorkspaceRepository  → firestore.Client (注入)
  │
  └── models/status.py (ステータス定義 — 全レイヤーから参照)
```

**原則**: 上位レイヤーは下位レイヤーのみに依存。同一レイヤー間の依存はなし。Firestoreへの直接アクセスはRepository層のみ。

---

## 7. SOLID違反の解消マッピング

| 違反 | 深刻度 | 解消セクション |
|------|--------|---------------|
| `main.py` が巨大 (SRP) | High | 5. main.py分割 |
| `AdminListener._generate_debug_report` 190行 (SRP) | High | 4-1. ReportService統合 |
| `NotificationService.send_daily_report` 3責務混在 (SRP) | High | 4-1. ReportService統合 |
| ステータス定義4+ファイル散在 (OCP) | High | 2. ステータス一元化 |
| レポートフォーマット2箇所コピペ (OCP) | High | 4-1. ReportService統合 |
| `AdminListener` がGroupService迂回 (LSP) | High | 3. Repository層 + 5. DI |
| `GroupService`/`WorkspaceService` Firestore直接結合 (DIP) | High | 3. Repository層 |
| `db.py` グローバルFirestoreクライアント (DIP) | High | 3. Repository層 |
| `AdminListener` Firestore直接生成 (DIP) | High | 3. Repository層 + 5. DI |
| `FirestoreInstallationStore` がmain.py内 (SRP) | Medium | 5. auth/分離 |
| `AttendanceListener` NLP+ビジネスロジック混在 (SRP) | Medium | 4-3. NlpService整理 |
| `nlp_service.py` 300行1関数 (SRP) | Medium | 4-3. NlpService整理 |
| Listenerコンストラクタ不統一 (LSP) | Medium | 5. Listener統一 |
| `NotificationService` 不要な依存強制 (ISP) | Medium | 4-1. NotificationService廃止 |
| `GroupService` 9メソッド肥大 (ISP) | Medium | 4-2. GroupService5メソッドに |
| `nlp_service.py` OpenAI直接生成 (DIP) | Medium | 4-3. NlpServiceクラス化 |
| `AdminListener` GroupService 6箇所直接生成 (DIP) | Medium | 5. DI |
