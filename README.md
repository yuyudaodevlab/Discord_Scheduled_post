# Discord 予約投稿Bot

Python 3.11以上とdiscord.py 2.xで動く、サーバー向けの予約投稿Botです。
Slash Command → 本文入力 → 本人だけに見える確認画面 →「予約する」の順に操作します。
JSONに保存するためデータベースは不要です。1プロセスで常時動かしてください。

## 1. 必要環境

- Python 3.11以上（検証環境：Python 3.12）
- インターネット接続、常時稼働できるPCやサーバー
- Botを招待できるDiscordサーバー
- 保存フォルダーへの書き込み権限

依存はdiscord.py、python-dotenv、tzdataです。tzdataはWindowsなどIANAタイムゾーン情報のない環境でも必要です。

## 2. DiscordでBotを作成

1. [Discord Developer Portal](https://discord.com/developers/applications) にログインします。
2. New Applicationからアプリケーションを作成します。
3. BotページでBotのTokenを発行・コピーします。Tokenはパスワードと同じ扱いにし、公開しないでください。
4. Privileged Gateway IntentsはすべてOFFのままで動きます。Message Content、Server Members、Presenceは不要です。
5. OAuth2のURL Generatorで `bot` と `applications.commands` を選択します。
6. Bot Permissionsで次を選択し、生成されたURLから招待します。

| Permission | 用途 |
| --- | --- |
| View Channels | 投稿先の閲覧 |
| Send Messages | 予約本文の投稿 |
| Embed Links | コマンド結果の表示 |
| Change Nickname | `/schedule-botname` |
| Mention Everyone（任意） | 権限のある予約者がeveryone/here・通知不可ロールに通知する場合 |

AdministratorはBotに不要です。チャンネル側の権限上書きも確認してください。
ユーザーにはUse Application Commandsと、投稿先でのView Channel / Send Messagesが必要です。
スレッド・フォーラムへの投稿は対象外です。通常のテキストチャンネル／アナウンスチャンネルを指定します。

## 3. インストール

このプロジェクトのフォルダーで実行します。`python --version` で3.11以上と確認してください。

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

macOS / Linux:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

`.env` をテキストエディタで開き、`DISCORD_BOT_TOKEN` にTokenを設定します。

```dotenv
DISCORD_BOT_TOKEN=ここに実際のToken
TEST_GUILD_ID=
DATA_PATH=data/data.json
MISSED_GRACE_SECONDS=300
```

`TEST_GUILD_ID` は開発時のテストサーバーIDです。Discordの開発者モードを有効にするとサーバーIDをコピーできます。
空欄なら全参加サーバー向けにコマンド登録します。全体登録の反映には時間がかかることがあります。
テスト登録から全体登録へ切り替えるとテスト側の登録が残る場合があるため、開発用と本番用のBotを分けると管理しやすくなります。
`DATA_PATH` の相対パスはプロジェクトフォルダー基準です。

## 4. 起動と停止

Windows:

```powershell
.\.venv\Scripts\python.exe bot.py
```

macOS / Linux:

```sh
.venv/bin/python bot.py
```

「Bot起動・接続完了」が表示されたらDiscordで `/schedule-post` を入力します。
停止はCtrl+Cです。PCのスリープ中・シャットダウン中は投稿できません。
常時運用はOSのサービス管理機能を使い、作業フォルダー・仮想環境・保存先を固定してください。
同じJSONを使う二重起動はOSのファイルロックで拒否します。別々のJSONコピーを同時稼働させないでください。

## 5. 使い方・コマンド

最初は `/schedule-post time:2m channel:#general` で試してください。
本文を入力し、確認画面の「予約する」で確定します。確認中はまだ保存されません。
相対時間はコマンドを実行した時点を基準にします。入力中に予定時刻が過ぎた場合は再作成してください。

| コマンド | 内容 |
| --- | --- |
| `/schedule-post time channel` | 本文をModalに入力し、確認して予約 |
| `/schedule-list` | 自分の予約を5件ずつ表示、前へ・次へで移動 |
| `/schedule-show reservation_id` | 本文全文・状態・通知時間・送信済みリンクを表示 |
| `/schedule-edit reservation_id [channel]` | 日時と本文をModalで編集。投稿先変更はchannelを指定 |
| `/schedule-cancel reservation_id` | 確認ボタンを押してキャンセル |
| `/schedule-notification add minutes` | テストDMが成功した場合だけ通知時間を追加 |
| `/schedule-notification remove minutes` | 通知時間を削除 |
| `/schedule-notification list` | 自分の通知設定を表示 |
| `/schedule-notification clear` | 通知設定を全解除 |
| `/schedule-notification test` | テストDMを送信 |
| `/schedule-timezone timezone` | サーバーのタイムゾーンを変更（管理者用） |
| `/schedule-botname name` | このサーバー内のBotニックネームを変更（管理者用） |

予約IDにはAutocompleteがあります。本人またはAdministrator / Manage Guild権限のある人だけが詳細・編集・キャンセルを操作できます。
管理者用コマンドは登録時と実行時に権限を制限しています。実行権限をDiscord側で公開しても実行時チェックは残ります。
編集・キャンセルできるのは予約中の投稿だけです。編集にも確認画面があります。
確認・一覧のボタンは3分、本文入力画面は5分で期限切れになります。

日時の例:

```text
2026-10-30 21:00
2026/10/30 21:00
2026-10-30 21:00:30
30秒後 / 10分後 / 2時間後 / 3日後
30s / 10m / 2h / 3d
```

内部保存はUTC、表示と絶対日時の解釈はサーバー設定（初期値Asia/Tokyo）です。
Discord Timestampは見る人の端末設定で表示されるため、併記したサーバー日時と異なる場合があります。
タイムゾーン変更は既存予約の実行時刻を変えません。夏時間で存在しない日時・二重に存在する日時は拒否します。

## 6. 本文・メンション・通知

- Modalの仕様に合わせて本文は最大4000文字です。改行・Markdown・絵文字・URL・メンション記法をそのまま保存します。
- 送信は2000文字相当（UTF-16でも上限以内）ごとに分割し、段落・改行を優先します。確認画面に送信数を表示します。
- 文字を追加・削除しないため、境界をまたぐコードブロック・spoilerなどは見た目が分断される場合があります。本文そのものは失われません。
- everyone/hereは予約確定時と送信時の両方で予約者にMention Everyoneがある場合だけ許可します。Bot自身のDiscord権限も必要です。
- ユーザーメンションは本文中の明示IDだけ、ロールメンションは通知可能なロール、または上記権限のある場合だけ許可します。各種IDは最大100件です。
- 確認画面やDMからメンション通知は発生させません。
- 投稿時にも予約者がサーバーに所属し、投稿先を閲覧・送信できる必要があります。
- DM設定はサーバー・ユーザーごとです。1～525600分、最大20件、重複は禁止します。
- DM設定は予約作成時にコピーします。設定変更は今後の予約だけに適用され、編集しても通知済みの時間は再通知しません。
- 予約作成時点で通知時刻が過ぎている場合、次回監視時に通知します。停止中に複数の通知時刻を過ぎた場合は各設定の通知が届きます。
- `notified` は送信試行前に保存します。DM拒否・通信失敗・直後の停止では通知が届かない場合がありますが、自動再試行による重複・連続送信はしません。
- 投稿失敗や期限超過は可能な限りDMします。成功DMは送りません。

## 7. JSONと復旧

初期JSONは `data/data.json` です。ファイルがない場合は初回起動時に生成します。
`version`、`guilds`、`users`、`schedules` を保存します。本文やユーザーIDを含むため、保存先を公開しないでください。
実行時JSONはGit管理対象外です。初期化用の中身は以下です。

```json
{"version": 1, "guilds": {}, "users": {}, "schedules": {}}
```

一時ファイルへの書き込み→flush→fsync→os.replaceで置換し、asyncio.Lockで並行更新を排他制御します。
ディスク書き込みは別スレッドで行います。変更直前の状態を `data/data.json.bak` に保存します。
通常JSONの読込・構造検証に失敗するとバックアップを試します。両方不正な場合はデータを上書きせず起動を止めます。
バックアップは1世代なので、別媒体への定期バックアップも推奨します。

状態は `scheduled`（予約中）、`sending`（送信中）、`sent`（投稿済み）、`failed`（失敗）、`cancelled`（キャンセル）、`missed`（期限超過）です。
再起動後、予定時刻から `MISSED_GRACE_SECONDS` 以内の予約は送信します。それ以上の予約はmissedになります。
稼働中でも処理遅延がこの時間を超えた予約は同様に扱います。

Discord送信とJSON保存を一つのトランザクションにできないため、厳密なexactly-once送信は保証できません。
このBotは重複回避を優先し、送信前にsendingを保存します。sendingのまま再起動した予約はfailedにし、自動再送しません。
長文の途中で失敗した場合も自動再送せず、保存できたMessage IDを詳細に表示します。
バックアップ復旧時は送信実績が古い可能性があるため、未完了予約をすべてfailedにして確認待ちとします。
この場合、管理者が一覧・詳細・実際の投稿先を確認し、未投稿のものだけ新規予約してください。復旧時はコンソールにも警告を出します。
Discord側や通信ライブラリの再試行に起因する重複まで完全には防げません。

履歴は自動削除しません。小～中規模向けで全体JSONを更新する方式です。大量運用では停止中にバックアップを作って終了済み履歴を整理してください。
実行中のJSON直接編集・共有ネットワークドライブでの運用は避けてください。

## 8. テスト

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

日時・夏時間・本文分割・JSON更新とバックアップ・予約状態を確認します。Bot Token不要です。
実サーバーでは短い予約、キャンセル、権限削除、DM拒否、再起動、長文を順に確認してください。

## 9. トラブルシューティング

| 症状 | 確認すること |
| --- | --- |
| コマンドが出ない | applications.commands付きの招待、TEST_GUILD_ID、登録反映待ち、Use Application Commands |
| 起動できない | Python 3.11以上か、仮想環境に依存を入れたか、Tokenが正しいか |
| 送信できない | Botと予約者のView Channel / Send Messages、チャンネル削除、サーバー脱退 |
| DMが届かない | サーバーメンバーからのDM許可、Botのブロック、notification test |
| 時刻が違う | schedule-timezone、PC時計、Timestampの端末タイムゾーン |
| ニックネームを変えられない | BotのChange Nickname権限、32文字以内か |
| 保存できない | ディスク空き容量、保存先の書き込み権限、ウイルス対策ソフトのファイルロック |
| JSONが壊れた | 原本を保管しバックアップ復旧ログを確認。両方壊れている場合は正常なコピーを復元 |
| 失敗・要確認になった | 詳細の原因と送信済みリンクを確認し、重複を避けて必要なものだけ新規予約 |

ログはコンソールへ出します。Tokenや投稿本文・DM本文をログへ出す処理はありません。

## 構成

- `bot.py`：起動、Discord接続、コマンド同期、停止
- `config.py`：環境変数
- `commands.py`：Slash Command、Modal、確認画面、ページ送り
- `scheduler.py`：予約操作、権限確認、送信、DM、監視
- `storage.py`：JSON検証、Atomic Write、復旧、二重起動防止
- `utils.py`：日時解釈、本文分割
- `tests/`：自動テスト

API参照：[discord.py公式ドキュメント](https://discordpy.readthedocs.io/en/stable/interactions/api.html)、[PyPI](https://pypi.org/project/discord.py/)
